from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.trial_cleanup import (
    LIVE_REGION_MIN_AGE_DAYS,
    LiveTrialRegionError,
    assert_safe_for_region,
    delete_trial_key,
    resolve_live_trial_region,
    select_trial_keys,
)
from app.db.models import (
    DBAuditLog,
    DBLimitedResource,
    DBPrivateAIKey,
    DBRegion,
    DBTeam,
    DBUser,
)
from app.schemas.limits import (
    LimitSource,
    LimitType,
    OwnerType,
    ResourceType,
    UnitType,
)


@pytest.fixture
def trial_team(db: Session):
    team = DBTeam(
        name="AI Trial Team", admin_email=settings.AI_TRIAL_TEAM_EMAIL, is_active=True
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def live_region(db: Session):
    """The region AI_TRIAL_REGION points at — new trials land here."""
    region = DBRegion(
        name=settings.AI_TRIAL_REGION,
        litellm_api_url="http://live-litellm",
        litellm_api_key="live-key",
        is_active=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


@pytest.fixture
def old_region(db: Session):
    """A region being decommissioned — safe to clean."""
    region = DBRegion(
        name="amazeeai-old-region",
        litellm_api_url="http://old-litellm",
        litellm_api_key="old-key",
        postgres_host="old-pg",
        is_active=False,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_trial_key(
    db: Session,
    team: DBTeam,
    region: DBRegion,
    *,
    email: str,
    created_at=None,
    spend: float | None = 0.0,
    with_database: bool = True,
):
    user = DBUser(email=email, team_id=team.id, is_active=True, role="user")
    db.add(user)
    db.commit()
    db.refresh(user)

    if spend is not None:
        db.add(
            DBLimitedResource(
                limit_type=LimitType.CONTROL_PLANE,
                resource=ResourceType.BUDGET,
                unit=UnitType.DOLLAR,
                max_value=2.0,
                current_value=spend,
                owner_type=OwnerType.USER,
                owner_id=user.id,
                limited_by=LimitSource.DEFAULT,
            )
        )

    key = DBPrivateAIKey(
        owner_id=user.id,
        team_id=team.id,
        region_id=region.id,
        litellm_token=f"sk-{email}",
        name=f"Trial Key {email}",
        database_name=f"db_{email.split('@')[0].replace('-', '_')}"
        if with_database
        else None,
        database_username=f"u_{email.split('@')[0].replace('-', '_')}"
        if with_database
        else None,
    )
    if created_at is not None:
        key.created_at = created_at
    db.add(key)
    db.commit()
    db.refresh(key)
    return user, key


# --- the guard ------------------------------------------------------------


def test_resolves_live_trial_region(db: Session, live_region: DBRegion):
    assert resolve_live_trial_region(db).id == live_region.id


def test_inactive_region_is_not_the_live_trial_region(
    db: Session, live_region: DBRegion
):
    """The trial endpoint filters on is_active, so the guard must too."""
    live_region.is_active = False
    db.commit()
    assert resolve_live_trial_region(db) is None


def test_guard_refuses_an_unfiltered_sweep_of_the_live_region(
    db: Session, live_region: DBRegion
):
    """The 2026-08-02 shape: take everything on the region issuing trials."""
    with pytest.raises(LiveTrialRegionError) as exc:
        assert_safe_for_region(db, live_region.id)
    assert settings.AI_TRIAL_REGION in str(exc.value)


def test_guard_refuses_the_live_region_without_the_unused_filter(
    db: Session, live_region: DBRegion
):
    with pytest.raises(LiveTrialRegionError):
        assert_safe_for_region(db, live_region.id, older_than_days=90)


def test_guard_refuses_the_live_region_without_an_age_filter(
    db: Session, live_region: DBRegion
):
    with pytest.raises(LiveTrialRegionError):
        assert_safe_for_region(db, live_region.id, unused_only=True)


def test_guard_refuses_too_young_an_age_on_the_live_region(
    db: Session, live_region: DBRegion
):
    with pytest.raises(LiveTrialRegionError) as exc:
        assert_safe_for_region(
            db,
            live_region.id,
            older_than_days=LIVE_REGION_MIN_AGE_DAYS - 1,
            unused_only=True,
        )
    assert str(LIVE_REGION_MIN_AGE_DAYS) in str(exc.value)


def test_guard_allows_an_old_unused_sweep_of_the_live_region(
    db: Session, live_region: DBRegion
):
    """The live region must stay reapable — it is the one that accumulates."""
    assert_safe_for_region(db, live_region.id, older_than_days=30, unused_only=True)


def test_guard_allows_anything_on_a_decommissioned_region(
    db: Session, live_region: DBRegion, old_region: DBRegion
):
    assert_safe_for_region(db, old_region.id)


def test_guard_allows_when_trial_region_does_not_resolve(
    db: Session, old_region: DBRegion
):
    """No region is issuing trials, so nothing live can be destroyed."""
    assert_safe_for_region(db, old_region.id)


# --- selection ------------------------------------------------------------


def test_selection_refuses_an_unfiltered_sweep_of_the_live_region(
    db: Session, trial_team: DBTeam, live_region: DBRegion
):
    """This is the 2026-08-02 incident: keys on the new trial region.

    The guard is inside select_trial_keys so a caller cannot reach the keys
    without tripping it.
    """
    _make_trial_key(db, trial_team, live_region, email="fresh@example.com")
    with pytest.raises(LiveTrialRegionError):
        select_trial_keys(db, live_region.id)


def test_selection_reaps_only_abandoned_keys_on_the_live_region(
    db: Session, trial_team: DBTeam, live_region: DBRegion
):
    """Fresh signups survive; long-abandoned ones on the same region do not."""
    now = datetime.now(UTC)
    _make_trial_key(
        db,
        trial_team,
        live_region,
        email="signed-up-today@example.com",
        created_at=now - timedelta(hours=2),
    )
    _, abandoned = _make_trial_key(
        db,
        trial_team,
        live_region,
        email="abandoned@example.com",
        created_at=now - timedelta(days=90),
    )

    selected = select_trial_keys(
        db, live_region.id, older_than_days=30, unused_only=True
    )

    assert [k.id for k in selected] == [abandoned.id]


def test_selection_is_scoped_to_one_region(
    db: Session, trial_team: DBTeam, live_region: DBRegion, old_region: DBRegion
):
    _, old_key = _make_trial_key(db, trial_team, old_region, email="old@example.com")
    _make_trial_key(db, trial_team, live_region, email="new@example.com")

    selected = select_trial_keys(db, old_region.id)

    assert [k.id for k in selected] == [old_key.id]


def test_selection_ignores_non_trial_keys(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    customer_team = DBTeam(
        name="Paying Customer", admin_email="customer@example.com", is_active=True
    )
    db.add(customer_team)
    db.commit()
    db.refresh(customer_team)

    _, trial_key = _make_trial_key(db, trial_team, old_region, email="t@example.com")
    _make_trial_key(db, customer_team, old_region, email="c@example.com")

    selected = select_trial_keys(db, old_region.id)

    assert [k.id for k in selected] == [trial_key.id]


def test_selection_filters_by_age(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    now = datetime.now(UTC)
    _, old_key = _make_trial_key(
        db,
        trial_team,
        old_region,
        email="ancient@example.com",
        created_at=now - timedelta(days=45),
    )
    _make_trial_key(
        db,
        trial_team,
        old_region,
        email="recent@example.com",
        created_at=now - timedelta(days=2),
    )

    selected = select_trial_keys(db, old_region.id, older_than_days=30)

    assert [k.id for k in selected] == [old_key.id]


def test_selection_skips_keys_with_spend(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    _, unused = _make_trial_key(
        db, trial_team, old_region, email="unused@example.com", spend=0.0
    )
    _make_trial_key(db, trial_team, old_region, email="spent@example.com", spend=0.42)

    selected = select_trial_keys(db, old_region.id, unused_only=True)

    assert [k.id for k in selected] == [unused.id]


def test_missing_budget_row_counts_as_unused(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    """A trial that never recorded anything is the normal never-used case."""
    _, key = _make_trial_key(
        db, trial_team, old_region, email="norow@example.com", spend=None
    )

    selected = select_trial_keys(db, old_region.id, unused_only=True)

    assert [k.id for k in selected] == [key.id]


# --- deletion -------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_remote_resources_then_rows(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    user, key = _make_trial_key(db, trial_team, old_region, email="gone@example.com")
    key_id, user_id = key.id, user.id

    litellm = AsyncMock()
    postgres = AsyncMock()

    result = await delete_trial_key(
        db,
        key,
        old_region,
        delete_user=True,
        litellm_service=litellm,
        postgres_manager=postgres,
    )

    assert result.ok
    assert result.litellm_deleted and result.database_deleted
    assert result.rows_deleted and result.user_deleted
    litellm.delete_key.assert_awaited_once_with("sk-gone@example.com")
    postgres.delete_database.assert_awaited_once()
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is None
    assert db.query(DBUser).filter_by(id=user_id).first() is None


@pytest.mark.asyncio
async def test_dead_litellm_leaves_every_row_in_place(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    """The de103 rule: a dead proxy must not cost us the row.

    Deleting the row while the LiteLLM key survives strands it with nothing
    left pointing at it.
    """
    _, key = _make_trial_key(db, trial_team, old_region, email="dead@example.com")
    key_id = key.id

    litellm = AsyncMock()
    litellm.delete_key.side_effect = httpx.ConnectError("connection refused")
    postgres = AsyncMock()

    result = await delete_trial_key(
        db, key, old_region, litellm_service=litellm, postgres_manager=postgres
    )

    assert not result.ok
    assert "litellm delete failed" in result.error
    assert not result.rows_deleted
    postgres.delete_database.assert_not_awaited()
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_dead_vector_db_host_leaves_rows_in_place(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    _, key = _make_trial_key(db, trial_team, old_region, email="nopg@example.com")
    key_id = key.id

    litellm = AsyncMock()
    postgres = AsyncMock()
    postgres.delete_database.side_effect = OSError("host unreachable")

    result = await delete_trial_key(
        db, key, old_region, litellm_service=litellm, postgres_manager=postgres
    )

    assert not result.ok
    assert "database drop failed" in result.error
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_user_kept_while_they_still_own_another_key(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    """A per-key loop must not delete a user whose other keys it has not reached."""
    user, key = _make_trial_key(db, trial_team, old_region, email="two@example.com")
    second = DBPrivateAIKey(
        owner_id=user.id,
        team_id=trial_team.id,
        region_id=old_region.id,
        litellm_token="sk-second",
        name="Second Key",
    )
    db.add(second)
    db.commit()
    user_id = user.id

    result = await delete_trial_key(
        db,
        key,
        old_region,
        delete_user=True,
        litellm_service=AsyncMock(),
        postgres_manager=AsyncMock(),
    )

    assert result.ok
    assert not result.user_deleted
    assert "still owns 1 key" in result.skipped_user_reason
    assert db.query(DBUser).filter_by(id=user_id).first() is not None


@pytest.mark.asyncio
async def test_audit_logs_are_detached_not_deleted(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    """audit_logs.user_id is a nullable FK with no ondelete.

    Deleting the rows would destroy the record of the signup; leaving them
    would block the user delete. Detaching keeps both.
    """
    user, key = _make_trial_key(db, trial_team, old_region, email="audit@example.com")
    db.add(
        DBAuditLog(
            user_id=user.id,
            event_type="POST",
            resource_type="private_ai_key",
            action="create",
            ip_address="203.0.113.7",
        )
    )
    db.commit()
    user_id = user.id

    result = await delete_trial_key(
        db,
        key,
        old_region,
        delete_user=True,
        litellm_service=AsyncMock(),
        postgres_manager=AsyncMock(),
    )

    assert result.ok and result.user_deleted
    assert db.query(DBUser).filter_by(id=user_id).first() is None
    log = db.query(DBAuditLog).filter_by(ip_address="203.0.113.7").one()
    assert log.user_id is None


@pytest.mark.asyncio
async def test_key_without_vector_db_skips_the_drop(
    db: Session, trial_team: DBTeam, old_region: DBRegion
):
    _, key = _make_trial_key(
        db, trial_team, old_region, email="nodb@example.com", with_database=False
    )
    postgres = AsyncMock()

    result = await delete_trial_key(
        db, key, old_region, litellm_service=AsyncMock(), postgres_manager=postgres
    )

    assert result.ok
    postgres.delete_database.assert_not_awaited()
