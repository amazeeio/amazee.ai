"""Region-scoped deletion of anonymous-trial keys and their resources.

Shared by the manual cleanup script and the scheduled reaper so the two cannot
drift apart. Two rules live here rather than in the callers, because a caller
that forgets either one causes damage that is not recoverable:

1. **Every destructive call names exactly one region, and that region may not
   be the live trial region.** On 2026-08-02 an ad-hoc script walked every
   region and expired 1,110 keys that had just been minted on the *new* trial
   region. ``select_trial_keys`` raises for the region ``AI_TRIAL_REGION``
   resolves to, so the guard cannot be skipped by forgetting to call it —
   there is no way to get a list of keys without going through it.

2. **A key row is deleted only once its remote resources are gone.** The
   LiteLLM key and the Postgres database outlive the row that points at them,
   so dropping the row first strands both with nothing left to find them by.
   If either remote call fails, ``delete_trial_key`` reports it and leaves
   every row in place to be retried. This is deliberately the opposite of
   ``hard_delete_expired_teams``, which logs remote failures and deletes the
   rows regardless.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.team_service import is_ai_trial_team
from app.db.models import (
    DBAuditLog,
    DBBudgetAlertState,
    DBLimitedResource,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.db.postgres import PostgresManager
from app.schemas.limits import OwnerType, ResourceType
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)


class LiveTrialRegionError(RuntimeError):
    """Raised when a destructive trial operation targets the live trial region.

    Deleting trial keys from the region that is currently issuing them takes
    working keys away from users who signed up minutes ago. Decommissioning a
    region always means pointing ``AI_TRIAL_REGION`` somewhere else first, so
    this condition only ever means the caller got the region wrong.
    """


def resolve_live_trial_region(db: Session) -> Optional[DBRegion]:
    """The region new trials are currently issued on, or None if unresolvable.

    Mirrors the resolution in ``generate_trial_access`` exactly, including the
    ``is_active`` filter and the numeric fallback that only fires for an
    all-digit value. Any divergence here would let the guard pass while live
    trials keep landing on the region being deleted.
    """
    region = (
        db.query(DBRegion)
        .filter(DBRegion.name == settings.AI_TRIAL_REGION, DBRegion.is_active)
        .first()
    )
    if not region and settings.AI_TRIAL_REGION.isdigit():
        region = (
            db.query(DBRegion)
            .filter(
                DBRegion.id == int(settings.AI_TRIAL_REGION),
                DBRegion.is_active,
            )
            .first()
        )
    return region


#: Youngest a key may be for the reaper to touch it on the region that is
#: currently issuing trials. The 2026-08-02 incident deleted keys minted the
#: same week; anything this old is abandoned, not in use.
LIVE_REGION_MIN_AGE_DAYS = 7


def assert_safe_for_region(
    db: Session,
    region_id: int,
    *,
    older_than_days: Optional[int] = None,
    unused_only: bool = False,
) -> None:
    """Raise ``LiveTrialRegionError`` for an unsafe sweep of the live region.

    The live trial region cannot simply be off limits: it is where trials are
    minted, so it is the region that actually accumulates them, and a reaper
    that skips it reaps nothing that matters.

    What must never happen is an *unfiltered* sweep of it — that is the
    2026-08-02 incident, where every trial key on the new trial region was
    destroyed including ones minted the same day. So a sweep of the live region
    is allowed only when it is narrowed to keys that are both old enough to be
    abandoned and have never recorded any spend. Every other region is
    unrestricted; it is not issuing trials, so nothing there can be in use by
    someone who signed up minutes ago.

    An unresolvable ``AI_TRIAL_REGION`` does not block anything: no region is
    issuing trials in that state. It is logged because it also means trial
    signups are 404ing.
    """
    live = resolve_live_trial_region(db)
    if live is None:
        logger.warning(
            "AI_TRIAL_REGION=%r does not resolve to an active region; "
            "trial signups are failing. Allowing cleanup of region %s.",
            settings.AI_TRIAL_REGION,
            region_id,
        )
        return
    if live.id != region_id:
        return

    if not unused_only or older_than_days is None:
        raise LiveTrialRegionError(
            f"Region {region_id} ({live.name}) is the live trial region "
            f"(AI_TRIAL_REGION={settings.AI_TRIAL_REGION!r}). It can only be "
            "swept with both an age filter and unused-only, so keys issued to "
            "people who just signed up are never touched."
        )
    if older_than_days < LIVE_REGION_MIN_AGE_DAYS:
        raise LiveTrialRegionError(
            f"Region {region_id} ({live.name}) is the live trial region and "
            f"older_than_days={older_than_days} is below the minimum of "
            f"{LIVE_REGION_MIN_AGE_DAYS}. Refusing: keys that young may still "
            "belong to active trials."
        )


def get_trial_team_ids(db: Session) -> list[int]:
    """Ids of every team that pools anonymous trial users.

    Normally exactly one. Returns a list because ``is_ai_trial_team`` matches on
    ``admin_email`` and a manually created duplicate would otherwise be missed.
    """
    return [team.id for team in db.query(DBTeam).all() if is_ai_trial_team(team)]


def select_trial_keys(
    db: Session,
    region_id: int,
    *,
    older_than_days: Optional[int] = None,
    unused_only: bool = False,
    limit: Optional[int] = None,
) -> list[DBPrivateAIKey]:
    """Trial keys in one region, oldest first.

    Always runs the live-region guard, so there is no way to obtain a list of
    keys to delete without it. The guard reads ``older_than_days`` and
    ``unused_only``: on the live trial region both are mandatory, everywhere
    else they are optional filters.

    A key belongs to a trial team by ``team_id`` or via its owner's ``team_id``
    — both are populated in practice, and matching only one silently misses
    keys.
    """
    assert_safe_for_region(
        db, region_id, older_than_days=older_than_days, unused_only=unused_only
    )

    trial_team_ids = get_trial_team_ids(db)
    if not trial_team_ids:
        return []

    query = (
        db.query(DBPrivateAIKey)
        .outerjoin(DBUser, DBUser.id == DBPrivateAIKey.owner_id)
        .filter(DBPrivateAIKey.region_id == region_id)
        .filter(
            or_(
                DBPrivateAIKey.team_id.in_(trial_team_ids),
                DBUser.team_id.in_(trial_team_ids),
            )
        )
    )

    if older_than_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        query = query.filter(DBPrivateAIKey.created_at < cutoff)

    if unused_only:
        query = query.filter(~_has_recorded_spend())

    query = query.order_by(DBPrivateAIKey.id)
    if limit is not None:
        query = query.limit(limit)

    return query.all()


def _has_recorded_spend():
    """SQL predicate: this key's owner has spend recorded against them.

    Must be applied in the query, never as a Python filter over the results.
    ``limit`` is a SQL LIMIT, so filtering afterwards would pick the batch
    first and shrink it second: keys with spend are permanently undeletable and
    sort to the front by id, so once their number exceeds the batch size every
    run would select the same undeletable prefix, filter it away, and reap
    nothing — forever.

    Reads the owner's BUDGET ``limited_resources`` row rather than asking
    LiteLLM, so filtering thousands of keys costs one join instead of a network
    round trip each. ``current_value`` is the figure the budget system
    maintains, and it is cumulative — LiteLLM's monthly ``budget_duration``
    reset does not roll it back — so it stays a safe signal of "this trial was
    used at some point".

    A missing row, a NULL ``current_value``, or a NULL ``owner_id`` all mean no
    spend was ever recorded, which is the normal state for the ~99.6% of trials
    that are never called.
    """
    return exists().where(
        DBLimitedResource.owner_type == OwnerType.USER,
        DBLimitedResource.owner_id == DBPrivateAIKey.owner_id,
        DBLimitedResource.resource == ResourceType.BUDGET,
        DBLimitedResource.current_value > 0,
    )


@dataclass
class SelectionRisk:
    """What is inside a selection that the operator should see before deleting.

    A retired region can be swept unfiltered — that is how a decommission
    drains one, and requiring filters there would make the job impossible. But
    a region only stopped issuing trials when ``AI_TRIAL_REGION`` was repointed,
    which may have been days ago, so its keys can still be young or in use. The
    count alone does not show that. This does, so the choice is informed rather
    than blind.
    """

    total: int = 0
    with_spend: int = 0
    younger_than_30d: int = 0

    @property
    def needs_attention(self) -> bool:
        return bool(self.with_spend or self.younger_than_30d)


def assess_selection(db: Session, keys: list[DBPrivateAIKey]) -> SelectionRisk:
    """Count keys in a selection that are used or recent."""
    risk = SelectionRisk(total=len(keys))
    if not keys:
        return risk

    cutoff = datetime.now(UTC) - timedelta(days=30)
    owner_ids = {k.owner_id for k in keys if k.owner_id is not None}
    spending_owners = set()
    if owner_ids:
        spending_owners = {
            row[0]
            for row in db.query(DBLimitedResource.owner_id)
            .filter(
                DBLimitedResource.owner_type == OwnerType.USER,
                DBLimitedResource.owner_id.in_(owner_ids),
                DBLimitedResource.resource == ResourceType.BUDGET,
                DBLimitedResource.current_value > 0,
            )
            .all()
        }

    for key in keys:
        if key.owner_id in spending_owners:
            risk.with_spend += 1
        created = key.created_at
        if created is not None:
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created >= cutoff:
                risk.younger_than_30d += 1
    return risk


@dataclass
class TrialKeyDeletion:
    """Outcome of deleting one trial key.

    ``error`` set means nothing was committed for this key and it can be
    retried unchanged.
    """

    key_id: int
    litellm_deleted: bool = False
    database_deleted: bool = False
    rows_deleted: bool = False
    user_deleted: bool = False
    error: Optional[str] = None
    skipped_user_reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class TrialCleanupSummary:
    deleted: int = 0
    failed: int = 0
    users_deleted: int = 0
    results: list[TrialKeyDeletion] = field(default_factory=list)

    def add(self, result: TrialKeyDeletion) -> None:
        self.results.append(result)
        if result.ok:
            self.deleted += 1
            if result.user_deleted:
                self.users_deleted += 1
        else:
            self.failed += 1


def key_has_recorded_spend(db: Session, key: DBPrivateAIKey) -> bool:
    """True when this key's owner has spend recorded against them.

    The single-key form of the SQL predicate used by ``select_trial_keys``, for
    the last-line check inside ``delete_trial_key``.
    """
    if key.owner_id is None:
        return False
    return db.query(
        db.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.USER,
            DBLimitedResource.owner_id == key.owner_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
            DBLimitedResource.current_value > 0,
        )
        .exists()
    ).scalar()


async def delete_trial_key(
    db: Session,
    key: DBPrivateAIKey,
    region: DBRegion,
    *,
    delete_user: bool = False,
    allow_used: bool = False,
    litellm_service: Optional[LiteLLMService] = None,
    postgres_manager: Optional[PostgresManager] = None,
) -> TrialKeyDeletion:
    """Delete one trial key and everything it owns, remote resources first.

    Order is load-bearing. The LiteLLM key and the Postgres database are the
    only two things that cost money and that nothing else records; the rows are
    just pointers to them. Remote first means a failure leaves a consistent,
    retryable state, and the row still says where to look.

    Both remote calls are idempotent — LiteLLM treats a 404 as success and the
    drop statements are ``IF EXISTS`` — so retrying a partially applied delete
    is safe.

    Services are injectable so the caller can build one per region instead of
    one per key.

    ``allow_used`` must be set explicitly to delete a key whose owner recorded
    spend. This is the last line of defence rather than the first: the selection
    query already excludes them when ``unused_only`` is on, and the reaper always
    sets it. It exists because a selection built any other way — an unfiltered
    sweep of a region that stopped issuing trials minutes ago, say — carries no
    such guarantee, and by this point the next statement is irreversible.
    """
    result = TrialKeyDeletion(key_id=key.id)

    if not allow_used and key_has_recorded_spend(db, key):
        result.error = "owner has recorded spend; pass allow_used=True to delete anyway"
        return result

    if key.litellm_token:
        service = litellm_service or LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        try:
            await service.delete_key(key.litellm_token)
            result.litellm_deleted = True
        except Exception as e:
            # Includes httpx.ConnectError when the proxy is already gone.
            # Deleting the row now would strand the key with no way to find it.
            result.error = f"litellm delete failed: {type(e).__name__}: {e}"
            return result
    else:
        result.litellm_deleted = True

    if key.database_name:
        manager = postgres_manager or PostgresManager(region=region)
        try:
            await manager.delete_database(key.database_name, key.database_username)
            result.database_deleted = True
        except Exception as e:
            result.error = f"database drop failed: {type(e).__name__}: {e}"
            return result
    else:
        result.database_deleted = True

    owner_id = key.owner_id
    try:
        _delete_key_rows(db, key)
        # Flush before counting the owner's remaining keys: the delete above is
        # still pending, and the session does not autoflush, so the count would
        # otherwise include the key we just removed and never free the user.
        db.flush()
        if delete_user and owner_id is not None:
            deleted, reason = _delete_trial_user(db, owner_id)
            result.user_deleted = deleted
            result.skipped_user_reason = reason
        db.commit()
        result.rows_deleted = True
    except Exception as e:
        db.rollback()
        result.error = f"row delete failed: {type(e).__name__}: {e}"
        result.user_deleted = False
        return result

    return result


def _delete_key_rows(db: Session, key: DBPrivateAIKey) -> None:
    """Remove the key row and everything with an FK pointing at it.

    ``spend_caps.key_id`` and ``budget_alert_state.key_id`` both reference
    ``ai_tokens.id`` with no ``ondelete``, so they are RESTRICT and must go
    first. ``team_spend_period_keys.key_id`` is ``ondelete="SET NULL"`` and
    looks after itself.
    """
    db.query(DBSpendCap).filter(DBSpendCap.key_id == key.id).delete(
        synchronize_session=False
    )
    db.query(DBBudgetAlertState).filter(DBBudgetAlertState.key_id == key.id).delete(
        synchronize_session=False
    )
    db.delete(key)


def _delete_trial_user(db: Session, user_id: int) -> tuple[bool, Optional[str]]:
    """Delete a trial user once their last key is gone.

    Returns ``(deleted, reason_skipped)``. A user with another key left is
    skipped rather than deleted, so a per-key loop cannot remove a user who
    still owns keys it has not reached yet.

    ``audit_logs.user_id`` is a nullable FK with no ``ondelete``, so the rows
    are detached rather than deleted — the log keeps its details, IP and
    timestamp, and only stops pointing at a row that no longer exists.
    """
    remaining = (
        db.query(DBPrivateAIKey).filter(DBPrivateAIKey.owner_id == user_id).count()
    )
    if remaining:
        return False, f"user still owns {remaining} key(s)"

    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if user is None:
        return False, "user row already gone"
    if not is_ai_trial_team(user.team):
        # Defensive: a non-trial owner means the selection query is wrong.
        return False, "owner is not in a trial team"

    db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.USER,
        DBLimitedResource.owner_id == user_id,
    ).delete(synchronize_session=False)
    db.query(DBSpendCap).filter(DBSpendCap.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(DBBudgetAlertState).filter(DBBudgetAlertState.user_id == user_id).delete(
        synchronize_session=False
    )
    db.query(DBAuditLog).filter(DBAuditLog.user_id == user_id).update(
        {DBAuditLog.user_id: None}, synchronize_session=False
    )
    db.delete(user)
    return True, None
