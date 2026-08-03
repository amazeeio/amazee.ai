from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.worker import reap_trial_keys
from app.db.models import (
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


def _trial_key(db, team, region, email, *, age_days, spend=0.0):
    user = DBUser(email=email, team_id=team.id, is_active=True, role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
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
        name=email,
        database_name=f"db_{email.split('@')[0].replace('-', '_')}",
        database_username=f"u_{email.split('@')[0].replace('-', '_')}",
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return user, key


@pytest.fixture
def patched_services():
    """Stub the two remote calls the reaper makes per key."""
    with (
        patch("app.core.worker.LiteLLMService") as litellm,
        patch("app.core.worker.PostgresManager") as postgres,
    ):
        litellm.return_value = AsyncMock()
        postgres.return_value = AsyncMock()
        yield litellm, postgres


@pytest.mark.asyncio
async def test_reaper_spares_fresh_keys_on_the_live_trial_region(
    db: Session, trial_team: DBTeam, live_region: DBRegion, patched_services
):
    """The 2026-08-02 incident, as a regression test.

    A key minted on the live trial region days ago belongs to someone who just
    signed up. It must survive the reaper.
    """
    _, key = _trial_key(db, trial_team, live_region, "fresh@example.com", age_days=3)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_reaper_does_reap_abandoned_keys_on_the_live_trial_region(
    db: Session, trial_team: DBTeam, live_region: DBRegion, patched_services
):
    """The live region is where trials pile up, so it must be reapable.

    Skipping it entirely would leave the reaper doing nothing useful.
    """
    _, key = _trial_key(db, trial_team, live_region, "stale@example.com", age_days=90)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 1
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is None


@pytest.mark.asyncio
async def test_reaper_skips_the_live_region_when_retention_is_too_short(
    db: Session, trial_team: DBTeam, live_region: DBRegion, patched_services, monkeypatch
):
    """A misconfigured retention must not become a mass deletion."""
    monkeypatch.setattr(settings, "AI_TRIAL_RETENTION_DAYS", 1)
    _, key = _trial_key(db, trial_team, live_region, "stale@example.com", age_days=90)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_reaper_deletes_old_unused_keys_on_other_regions(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    user, key = _trial_key(db, trial_team, old_region, "old@example.com", age_days=90)
    key_id, user_id = key.id, user.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 1
    assert summary.users_deleted == 1
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is None
    assert db.query(DBUser).filter_by(id=user_id).first() is None


@pytest.mark.asyncio
async def test_reaper_keeps_recent_trials(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    _, key = _trial_key(db, trial_team, old_region, "recent@example.com", age_days=2)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_reaper_keeps_trials_that_spent_money(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    """Any recorded spend means a real user, however small the amount."""
    _, key = _trial_key(
        db, trial_team, old_region, "spent@example.com", age_days=90, spend=0.01
    )
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_reaper_keeps_rows_when_the_region_is_unreachable(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    """A dead proxy must cost us nothing — the rows are the only pointer left."""
    litellm, _ = patched_services
    litellm.return_value.delete_key.side_effect = OSError("connection refused")

    _, key = _trial_key(db, trial_team, old_region, "dead@example.com", age_days=90)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert summary.failed == 1
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None


@pytest.mark.asyncio
async def test_reaper_stops_after_repeated_failures_in_one_region(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    """Ten failures means the host is down; do not retry it thousands of times."""
    litellm, _ = patched_services
    litellm.return_value.delete_key.side_effect = OSError("connection refused")

    for i in range(15):
        _trial_key(db, trial_team, old_region, f"dead{i}@example.com", age_days=90)

    summary = await reap_trial_keys(db)

    assert summary.failed == 10
    assert db.query(DBPrivateAIKey).count() == 15


@pytest.mark.asyncio
async def test_reaper_respects_the_batch_size(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
    monkeypatch,
):
    monkeypatch.setattr(settings, "AI_TRIAL_REAP_BATCH_SIZE", 3)
    for i in range(10):
        _trial_key(db, trial_team, old_region, f"old{i}@example.com", age_days=90)

    summary = await reap_trial_keys(db)

    assert summary.deleted == 3
    assert db.query(DBPrivateAIKey).count() == 7


@pytest.mark.asyncio
async def test_reaper_ignores_non_trial_keys(
    db: Session,
    trial_team: DBTeam,
    live_region: DBRegion,
    old_region: DBRegion,
    patched_services,
):
    customer = DBTeam(
        name="Paying Customer", admin_email="customer@example.com", is_active=True
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    _, key = _trial_key(db, customer, old_region, "cust@example.com", age_days=365)
    key_id = key.id

    summary = await reap_trial_keys(db)

    assert summary.deleted == 0
    assert db.query(DBPrivateAIKey).filter_by(id=key_id).first() is not None
