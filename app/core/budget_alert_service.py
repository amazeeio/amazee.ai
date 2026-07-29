"""Budget threshold alerts — detect when spend crosses a share of budget.

Emits an event when a team, key or team member crosses 50 / 75 / 90 / 100 % of
its budget, so a downstream consumer (MOAD) can warn the customer *before* their
keys stop working.

Why amazee.ai computes the percentage instead of LiteLLM
-------------------------------------------------------
LiteLLM has native budget alerting, but it cannot produce the right number for
our teams:

* **Wrong numerator for PERIODIC teams.** LiteLLM's team-level ``spend`` counter
  is never reset; our billing cycle zeroes the *per-key* spends instead. Dividing
  a lifetime total by a one-cycle budget sits permanently above 100 %. The spend
  API works around this by summing key spends (``app/api/spend.py``), and so do we.
* **Moving denominator for POOL teams.** The budget is owned by our ledger and
  pushed to LiteLLM. Buying a top-up raises it, so a percentage can fall back
  below a band it already crossed. LiteLLM's TTL-based dedup would suppress the
  genuine second crossing.
* Its thresholds are hardcoded to 85 % / 95 % and are not configurable.

So LiteLLM is the source of *spend*, and every budget number comes from our DB.

How it scales to 100k keys
--------------------------
Spend is read from ``/team/daily/activity`` and ``/user/daily/activity`` with no
entity filter — two calls per region, regardless of key count. LiteLLM only
writes daily-spend rows for entities that actually spent, so the cost tracks
*active* entities rather than total keys. Enumerating every key instead (the
shape of the existing hourly reconciler) is what does not survive 100k.

The one place we still enumerate keys is a scoped ``/key/list?team_id=`` for a
team we have already decided is worth confirming — see ``_exact_team_key_state``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from prometheus_client import Counter, Gauge, Summary
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.pool_budget_service import pool_available_budget_for_team_region
from app.core.spend_period_service import TeamPeriodWindow, resolve_team_period_window
from app.core.team_service import get_team_region_litellm_keys
from app.db.models import (
    DBBudgetAlertState,
    DBLimitedResource,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.schemas.limits import OwnerType, ResourceType
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

SUBJECT_TEAM = "team"
SUBJECT_KEY = "key"
SUBJECT_TEAM_MEMBER = "team_member"

budget_alerts_fired_total = Counter(
    "budget_alerts_fired_total",
    "Budget threshold crossings detected",
    ["scope", "threshold"],
)

budget_alert_delivery_failures_total = Counter(
    "budget_alert_delivery_failures_total",
    "Budget threshold events that were detected but not delivered",
)

budget_alert_subjects_evaluated = Gauge(
    "budget_alert_subjects_evaluated",
    "Subjects evaluated in the last budget threshold sweep, per region",
    ["region_name"],
)

budget_alert_run_duration = Summary(
    "budget_alert_run_duration_seconds",
    "Time taken to complete the budget threshold sweep",
)

# LiteLLM's own bookkeeping teams show up in the activity breakdown; they are not
# amazee.ai teams and have no budget of ours.
_NON_TEAM_ENTITY_IDS = frozenset({"litellm-dashboard"})


def parse_thresholds(raw: str | None = None) -> list[int]:
    """Parse the configured thresholds into a sorted, de-duplicated list."""
    raw = raw if raw is not None else settings.BUDGET_ALERT_THRESHOLDS
    values: set[int] = set()
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(float(chunk))
        except (TypeError, ValueError):
            logger.warning("Ignoring unparseable budget alert threshold: %r", chunk)
            continue
        if 0 < value <= 1000:
            values.add(value)
    return sorted(values)


def highest_crossed_band(percent_used: float, thresholds: list[int]) -> int:
    """Return the highest threshold at or below ``percent_used`` (0 if none).

    Only the highest band is ever reported, so a key that jumps from 0 % to 95 %
    in one tick produces a single 90 % event rather than 50 + 75 + 90.
    """
    crossed = 0
    for threshold in thresholds:
        if percent_used >= threshold:
            crossed = threshold
    return crossed


@dataclass(frozen=True)
class BudgetAlertEvent:
    """One threshold crossing, ready to serialise for the webhook."""

    event_id: str
    subject_type: str
    subject_key: str
    threshold_pct: int
    percent_used: float
    spend: float
    max_budget: float
    region_id: int
    region_name: str
    period_key: str
    period_start: datetime | None
    period_end: datetime | None
    budget_duration: str | None
    team_id: int | None = None
    team_name: str | None = None
    user_id: int | None = None
    user_email: str | None = None
    key_id: int | None = None
    key_name: str | None = None


@dataclass
class _Subject:
    """A thing with a budget, evaluated against the thresholds."""

    subject_type: str
    subject_key: str
    spend: float
    max_budget: float | None
    window: TeamPeriodWindow
    team: DBTeam | None = None
    user: DBUser | None = None
    key: DBPrivateAIKey | None = None

    @property
    def percent_used(self) -> float:
        if not self.max_budget or self.max_budget <= 0:
            return 0.0
        return round((self.spend / self.max_budget) * 100.0, 4)


@dataclass
class RegionEvaluation:
    """Result of evaluating one region."""

    events: list[BudgetAlertEvent] = field(default_factory=list)
    # Subjects whose band dropped or whose period rolled over. Their state is
    # rewritten without notifying, so a later genuine re-crossing still fires.
    resets: list[tuple[_Subject, int]] = field(default_factory=list)
    # Every subject considered this tick, kept so a delivered event can be
    # re-joined to the ORM objects behind it. Events stay plain data because they
    # get serialised; subjects hold ORM instances and never leave this module.
    subjects: list[_Subject] = field(default_factory=list)
    litellm_calls: int = 0

    @property
    def subjects_evaluated(self) -> int:
        return len(self.subjects)


# --------------------------------------------------------------------------- #
# Spend collection
# --------------------------------------------------------------------------- #


def _sum_activity_by_entity(
    rows: list[dict], since: date | None
) -> tuple[dict[str, float], dict[str, float], date | None]:
    """Fold daily-activity rows into per-entity and per-key spend totals.

    ``rows`` is one entry per UTC day. Days before ``since`` are skipped so each
    team is only credited with spend inside its own billing window, even though
    a single sweep covers every team's window at once.

    Returns ``(entity_spend, key_spend, earliest_day_seen)``. The third value
    lets the caller tell whether the sweep actually reached back to the start of
    a window, or whether the total it just computed is partial.
    """
    entity_spend: dict[str, float] = {}
    key_spend: dict[str, float] = {}
    earliest: date | None = None

    for row in rows:
        raw_date = row.get("date")
        try:
            row_date = date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            logger.warning("Skipping daily-activity row with bad date: %r", raw_date)
            continue
        if since is not None and row_date < since:
            continue
        if earliest is None or row_date < earliest:
            earliest = row_date

        breakdown = row.get("breakdown") or {}
        for entity_id, payload in (breakdown.get("entities") or {}).items():
            metrics = (payload or {}).get("metrics") or {}
            entity_spend[entity_id] = entity_spend.get(entity_id, 0.0) + float(
                metrics.get("spend") or 0.0
            )
        for hashed_token, payload in (breakdown.get("api_keys") or {}).items():
            metrics = (payload or {}).get("metrics") or {}
            key_spend[hashed_token] = key_spend.get(hashed_token, 0.0) + float(
                metrics.get("spend") or 0.0
            )

    return entity_spend, key_spend, earliest


def _amazee_team_id_from_litellm(entity_id: str, region_name: str) -> int | None:
    """Recover our team id from LiteLLM's ``<region>_<team_id>`` team id."""
    if entity_id in _NON_TEAM_ENTITY_IDS:
        return None
    prefix = f"{region_name.replace(' ', '_')}_"
    if not entity_id.startswith(prefix):
        return None
    suffix = entity_id[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


async def _exact_team_key_state(
    service: LiteLLMService, lite_team_id: str
) -> dict[str, dict]:
    """Current per-key ``spend``/``max_budget`` for one team, keyed by hashed token.

    Needed when the daily-activity sweep cannot answer on its own:

    * a POOL window that opened before the sweep's lookback (its total would be
      partial) — LiteLLM's key spend is the pool-lifetime spend, which is exactly
      what a non-resetting pool window wants;
    * PERIODIC keys, whose authoritative ``max_budget`` lives in LiteLLM rather
      than in our ``spend_caps``.
    """
    try:
        keys = await service.list_keys_for_team(lite_team_id)
    except Exception as exc:
        logger.warning("Could not fetch exact key state for %s: %s", lite_team_id, exc)
        return {}
    return {
        str(key.get("token")): key
        for key in keys
        if isinstance(key, dict) and key.get("token")
    }


# --------------------------------------------------------------------------- #
# Denominators — always ours, never LiteLLM's
# --------------------------------------------------------------------------- #


def _team_budget(db: Session, team: DBTeam, region_id: int) -> float | None:
    """Budget available to a team in a region, from our ledger.

    Mirrors what the spend API reports as ``period_budget``: remaining
    subscription entries plus remaining top-ups. An operator cap
    (``spend_caps`` scope ``team``) only ever tightens it.
    """
    available = pool_available_budget_for_team_region(db, team.id, region_id)
    if available <= 0:
        # PERIODIC teams with no ledger yet (e.g. trials) still carry a BUDGET
        # limit, which is the only budget they have.
        limit_value = (
            db.query(DBLimitedResource.max_value)
            .filter(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id,
                DBLimitedResource.resource == ResourceType.BUDGET,
            )
            .scalar()
        )
        available = float(limit_value) if limit_value else 0.0

    cap = (
        db.query(DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team.id,
            DBSpendCap.max_budget.isnot(None),
        )
        .scalar()
    )
    if cap is not None:
        available = min(available, float(cap))
    return available if available > 0 else None


def _member_budget(
    db: Session, team_id: int, user_id: int, region_id: int
) -> float | None:
    """Per-member budget: the team-member cap, else the user's BUDGET limit."""
    cap = (
        db.query(DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team_id,
            DBSpendCap.user_id == user_id,
            DBSpendCap.max_budget.isnot(None),
        )
        .scalar()
    )
    if cap is not None:
        return float(cap) if float(cap) > 0 else None

    limit_value = (
        db.query(DBLimitedResource.max_value)
        .filter(
            DBLimitedResource.owner_type == OwnerType.USER,
            DBLimitedResource.owner_id == user_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .scalar()
    )
    if limit_value and float(limit_value) > 0:
        return float(limit_value)
    return None


def _key_cap_map(db: Session, region_id: int, key_ids: list[int]) -> dict[int, float]:
    if not key_ids:
        return {}
    rows = (
        db.query(DBSpendCap.key_id, DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == region_id,
            DBSpendCap.key_id.in_(key_ids),
            DBSpendCap.max_budget.isnot(None),
        )
        .all()
    )
    return {int(key_id): float(value) for key_id, value in rows if value}


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _sweep_start(now: datetime) -> date:
    return (now - timedelta(days=settings.BUDGET_ALERT_MAX_LOOKBACK_DAYS)).date()


def _window_since(window: TeamPeriodWindow, sweep_start: date) -> date:
    if window.period_start is None:
        return sweep_start
    return max(window.period_start.date(), sweep_start)


def _is_expiring(key_state: dict) -> bool:
    """A key set to ``0d`` is being expired; it is not a budget problem."""
    return str(key_state.get("budget_duration") or "") == "0d"


async def evaluate_region(
    db: Session,
    region: DBRegion,
    *,
    thresholds: list[int] | None = None,
    now: datetime | None = None,
) -> RegionEvaluation:
    """Evaluate every active subject in one region against the thresholds."""
    thresholds = thresholds or parse_thresholds()
    now = now or datetime.now(UTC)
    result = RegionEvaluation()
    if not thresholds:
        return result

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    sweep_start = _sweep_start(now)
    start_str = sweep_start.isoformat()
    end_str = now.date().isoformat()

    try:
        team_rows, user_rows = await asyncio.gather(
            service.get_all_team_daily_activity(start_str, end_str),
            service.get_all_user_daily_activity(start_str, end_str),
        )
        result.litellm_calls += 2
    except Exception as exc:
        logger.error("Budget alert sweep failed for region %s: %s", region.name, exc)
        return result

    subjects: list[_Subject] = []

    # --- candidate teams --------------------------------------------------- #
    # Only entities that actually spent appear in the sweep, so this is the
    # working set: on a DEV region with 2,740 keys it was 27 teams. A team with
    # no traffic in the lookback cannot have newly crossed a band, since spend
    # only rises with traffic and we notify on upward moves only.
    team_totals_all, _, _ = _sum_activity_by_entity(team_rows, None)
    candidate_team_ids = {
        team_id
        for entity_id in team_totals_all
        if (team_id := _amazee_team_id_from_litellm(entity_id, region.name)) is not None
    }

    # A key owned by a user but not attached to a LiteLLM team contributes no
    # team entity, so its team would be invisible above. The user sweep closes
    # that gap: active user ids map back to their team.
    active_user_totals, _, _ = _sum_activity_by_entity(user_rows, None)
    active_user_ids = [
        int(user_id) for user_id in active_user_totals if str(user_id).isdigit()
    ]
    if active_user_ids:
        candidate_team_ids.update(
            team_id
            for (team_id,) in db.query(DBUser.team_id)
            .filter(DBUser.id.in_(active_user_ids), DBUser.team_id.isnot(None))
            .distinct()
            .all()
        )

    if not candidate_team_ids:
        logger.info("No active teams in region %s this sweep", region.name)
        return result

    teams = (
        db.query(DBTeam)
        .filter(DBTeam.id.in_(candidate_team_ids), DBTeam.deleted_at.is_(None))
        .all()
    )
    for team in teams:
        window = resolve_team_period_window(db, team, region.id, now=now)
        since = _window_since(window, sweep_start)
        team_totals, key_totals, _ = _sum_activity_by_entity(team_rows, since)
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

        # Whole-UTC-day granularity and a lookback shorter than the window both
        # make the swept total partial. POOL windows run for
        # POOL_PURCHASE_EXPIRY_DAYS (365 by default), well past the lookback, so
        # for those we take LiteLLM's live key spend instead: a pool window never
        # resets, so key spend *is* the window total.
        window_predates_sweep = (
            window.period_start is not None and window.period_start.date() < sweep_start
        )
        needs_exact_keys = window_predates_sweep or team.budget_type != BudgetType.POOL
        exact_keys = (
            await _exact_team_key_state(service, lite_team_id)
            if needs_exact_keys
            else {}
        )
        if exact_keys:
            result.litellm_calls += 1

        # Includes keys owned by team members but not attached to the team, which
        # is the same ownership rule the spend API and the billing cycle use.
        db_keys = get_team_region_litellm_keys(db, team_id=team.id, region_id=region.id)
        cap_map = _key_cap_map(db, region.id, [key.id for key in db_keys])

        team_spend = float(team_totals.get(lite_team_id, 0.0))
        member_spend: dict[int, float] = {}

        for db_key in db_keys:
            hashed = LiteLLMService.hash_token(db_key.litellm_token)
            key_state = exact_keys.get(hashed)
            if key_state is not None:
                if _is_expiring(key_state):
                    continue
                key_spend = float(key_state.get("spend") or 0.0)
                litellm_max_budget = key_state.get("max_budget")
            else:
                key_spend = float(key_totals.get(hashed, 0.0))
                litellm_max_budget = None

            if db_key.owner_id:
                member_spend[db_key.owner_id] = (
                    member_spend.get(db_key.owner_id, 0.0) + key_spend
                )

            # A DB cap wins; otherwise fall back to what LiteLLM enforces.
            key_budget = cap_map.get(db_key.id)
            if key_budget is None and litellm_max_budget is not None:
                key_budget = float(litellm_max_budget)
            # max_budget of 0 is how a pool-gated key is born blocked. Alerting
            # 100 % at creation would be pure noise, so an absent or
            # non-positive budget means "no key-level threshold".
            if not key_budget or key_budget <= 0:
                continue

            subjects.append(
                _Subject(
                    subject_type=SUBJECT_KEY,
                    subject_key=f"key:{db_key.id}",
                    spend=key_spend,
                    max_budget=key_budget,
                    window=window,
                    team=team,
                    key=db_key,
                )
            )

        if exact_keys:
            # Prefer the live sum for the team as well, so the team percentage and
            # its keys' percentages are drawn from the same numbers.
            team_spend = sum(
                float(state.get("spend") or 0.0)
                for state in exact_keys.values()
                if not _is_expiring(state)
            )

        subjects.append(
            _Subject(
                subject_type=SUBJECT_TEAM,
                subject_key=f"team:{team.id}:{region.id}",
                spend=team_spend,
                max_budget=_team_budget(db, team, region.id),
                window=window,
                team=team,
            )
        )

        # --- team members -------------------------------------------------- #
        if member_spend:
            users = (
                db.query(DBUser).filter(DBUser.id.in_(list(member_spend.keys()))).all()
            )
            for user in users:
                subjects.append(
                    _Subject(
                        subject_type=SUBJECT_TEAM_MEMBER,
                        subject_key=f"member:{team.id}:{user.id}:{region.id}",
                        spend=member_spend.get(user.id, 0.0),
                        max_budget=_member_budget(db, team.id, user.id, region.id),
                        window=window,
                        team=team,
                        user=user,
                    )
                )

    result.subjects = subjects
    _classify_subjects(db, region, subjects, thresholds, result)
    return result


def _classify_subjects(
    db: Session,
    region: DBRegion,
    subjects: list[_Subject],
    thresholds: list[int],
    result: RegionEvaluation,
) -> None:
    """Split subjects into new crossings to notify and states to quietly reset."""
    if not subjects:
        return

    existing = {
        row.subject_key: row
        for row in db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key.in_([s.subject_key for s in subjects]))
        .all()
    }

    for subject in subjects:
        if not subject.max_budget or subject.max_budget <= 0:
            continue
        band = highest_crossed_band(subject.percent_used, thresholds)
        state = existing.get(subject.subject_key)
        period_key = subject.window.period_key

        previous_band = 0
        if state is not None and state.period_key == period_key:
            previous_band = int(state.last_threshold_pct or 0)

        if band > previous_band:
            result.events.append(_build_event(region, subject, band, period_key))
        elif band != previous_band or (
            state is not None and state.period_key != period_key
        ):
            # Either the period rolled over, or the percentage fell — a POOL
            # top-up raises the denominator. Rewrite the band silently so a real
            # re-crossing later still notifies, but never announce a decrease.
            result.resets.append((subject, band))


def _build_event(
    region: DBRegion, subject: _Subject, band: int, period_key: str
) -> BudgetAlertEvent:
    return BudgetAlertEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        subject_type=subject.subject_type,
        subject_key=subject.subject_key,
        threshold_pct=band,
        percent_used=subject.percent_used,
        spend=round(subject.spend, 4),
        max_budget=round(float(subject.max_budget or 0.0), 4),
        region_id=region.id,
        region_name=region.name,
        period_key=period_key,
        period_start=subject.window.period_start,
        period_end=subject.window.period_end,
        budget_duration=subject.window.budget_duration,
        team_id=subject.team.id if subject.team else None,
        team_name=subject.team.name if subject.team else None,
        user_id=subject.user.id if subject.user else None,
        user_email=subject.user.email if subject.user else None,
        key_id=subject.key.id if subject.key else None,
        key_name=subject.key.name if subject.key else None,
    )


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #


def _upsert_state(
    db: Session,
    *,
    subject: _Subject,
    region_id: int,
    band: int,
    period_key: str,
    notified_at: datetime | None,
) -> None:
    row = (
        db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key == subject.subject_key)
        .first()
    )
    if row is None:
        row = DBBudgetAlertState(
            subject_key=subject.subject_key,
            subject_type=subject.subject_type,
            region_id=region_id,
        )
        db.add(row)
    row.subject_type = subject.subject_type
    row.region_id = region_id
    row.team_id = subject.team.id if subject.team else None
    row.user_id = subject.user.id if subject.user else None
    row.key_id = subject.key.id if subject.key else None
    row.period_key = period_key
    row.last_threshold_pct = band
    row.spend_at_notify = round(subject.spend, 4)
    row.budget_at_notify = round(float(subject.max_budget or 0.0), 4)
    row.percent_at_notify = subject.percent_used
    if notified_at is not None:
        row.notified_at = notified_at


def apply_resets(db: Session, region: DBRegion, evaluation: RegionEvaluation) -> None:
    """Persist silent band/period rewrites. Safe to run before delivery."""
    for subject, band in evaluation.resets:
        _upsert_state(
            db,
            subject=subject,
            region_id=region.id,
            band=band,
            period_key=subject.window.period_key,
            notified_at=None,
        )
    if evaluation.resets:
        db.commit()


def mark_notified(
    db: Session,
    region: DBRegion,
    evaluation: RegionEvaluation,
    delivered_event_ids: set[str],
    now: datetime | None = None,
) -> int:
    """Advance state for the events that were actually delivered.

    Called *after* delivery on purpose. If the webhook fails, the band is left
    where it was and the next tick re-detects the same crossing, which gives
    at-least-once delivery without an outbox table. Consumers de-duplicate on
    ``event_id``.
    """
    now = now or datetime.now(UTC)
    by_subject = {
        event.subject_key: event
        for event in evaluation.events
        if event.event_id in delivered_event_ids
    }
    if not by_subject:
        return 0

    advanced = 0
    for subject in evaluation.subjects:
        event = by_subject.get(subject.subject_key)
        if event is None:
            continue
        _upsert_state(
            db,
            subject=subject,
            region_id=region.id,
            band=event.threshold_pct,
            period_key=event.period_key,
            notified_at=now,
        )
        advanced += 1
    db.commit()
    return advanced


# --------------------------------------------------------------------------- #
# Job entry point
# --------------------------------------------------------------------------- #


@budget_alert_run_duration.time()
async def monitor_budget_thresholds(db: Session) -> dict[str, int]:
    """Sweep every active region, notify new crossings, and record what stuck.

    Regions are independent, so they are swept concurrently — but each region's
    DB work runs on the shared session, so evaluation is serialised per region
    and only the LiteLLM reads overlap.
    """
    from app.services.budget_alert_webhook import deliver_events

    thresholds = parse_thresholds()
    if not thresholds:
        logger.warning("No budget alert thresholds configured; nothing to do")
        return {"regions": 0, "events": 0, "delivered": 0}

    regions = (
        db.query(DBRegion)
        .filter(DBRegion.is_active.is_(True))
        .order_by(DBRegion.id)
        .all()
    )
    logger.info(
        "Budget threshold sweep starting: %d region(s), thresholds=%s, enabled=%s",
        len(regions),
        thresholds,
        settings.BUDGET_ALERT_ENABLED,
    )

    totals = {
        "regions": 0,
        "subjects": 0,
        "events": 0,
        "delivered": 0,
        "litellm_calls": 0,
    }

    semaphore = asyncio.Semaphore(max(1, settings.BUDGET_ALERT_REGION_CONCURRENCY))

    async def _evaluate(region: DBRegion) -> tuple[DBRegion, RegionEvaluation | None]:
        async with semaphore:
            try:
                return region, await evaluate_region(db, region, thresholds=thresholds)
            except Exception as exc:
                logger.error(
                    "Budget threshold evaluation failed for region %s: %s",
                    region.name,
                    exc,
                    exc_info=True,
                )
                return region, None

    for region, evaluation in await asyncio.gather(
        *[_evaluate(region) for region in regions]
    ):
        if evaluation is None:
            continue
        totals["regions"] += 1
        totals["subjects"] += evaluation.subjects_evaluated
        totals["events"] += len(evaluation.events)
        totals["litellm_calls"] += evaluation.litellm_calls
        budget_alert_subjects_evaluated.labels(region_name=region.name).set(
            evaluation.subjects_evaluated
        )
        for event in evaluation.events:
            budget_alerts_fired_total.labels(
                scope=event.subject_type, threshold=str(event.threshold_pct)
            ).inc()

        # Silent rewrites are unconditional: they carry no notification, so
        # holding them back would only cause a stale band to suppress a later
        # genuine crossing.
        apply_resets(db, region, evaluation)

        if not evaluation.events:
            continue

        for event in evaluation.events:
            logger.info(
                "Budget threshold crossed: scope=%s subject=%s %.2f%% "
                "(spend=%.4f budget=%.4f) band=%d%% region=%s",
                event.subject_type,
                event.subject_key,
                event.percent_used,
                event.spend,
                event.max_budget,
                event.threshold_pct,
                event.region_name,
            )

        if not settings.BUDGET_ALERT_ENABLED:
            # Log-only mode: percentages can be validated against the spend API
            # before any third party is told anything. State is left untouched so
            # enabling delivery later still sends these crossings.
            continue

        delivered = await deliver_events(evaluation.events)
        totals["delivered"] += len(delivered)
        undelivered = len(evaluation.events) - len(delivered)
        if undelivered > 0:
            budget_alert_delivery_failures_total.inc(undelivered)
        mark_notified(db, region, evaluation, delivered)

    logger.info(
        "Budget threshold sweep finished: regions=%d subjects=%d events=%d "
        "delivered=%d litellm_calls=%d",
        totals["regions"],
        totals["subjects"],
        totals["events"],
        totals["delivered"],
        totals["litellm_calls"],
    )
    return totals
