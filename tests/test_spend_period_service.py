from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import settings
from app.core.spend_period_service import (
    canonical_budget_duration,
    compute_period_start,
    resolve_team_period_window,
    settle_topup_spend,
    upsert_team_spend_period,
)
from app.db.models import (
    DBPeriodicBudgetLedgerEntry,
    DBTeamSpendPeriod,
    DBTeamSpendPeriodKey,
)
from app.schemas.models import BudgetType


def test_upsert_team_spend_period_creates_parent_and_keys(
    db, test_team, test_region, test_team_user
):
    period_start = datetime(2026, 4, 1, tzinfo=UTC)
    period_end = datetime(2026, 5, 1, tzinfo=UTC)

    snapshot = {
        "total_spend": 12.34,
        "total_budget": 50.0,
        "total_prompt_tokens": 100,
        "total_completion_tokens": 200,
        "total_tokens": 300,
        "keys": [
            {
                "key_id": None,
                "owner_id": None,
                "key_name_snapshot": "k1",
                "spend": 10.0,
                "max_budget": 25.0,
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
            {
                "key_id": None,
                "owner_id": test_team_user.id,
                "key_name_snapshot": "k2",
                "spend": 2.34,
                "max_budget": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
        ],
    }

    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot,
        stripe_event_id="evt_1",
        subscription_remaining_cents=120,
        topup_remaining_cents=30,
        desired_remaining_cents=150,
    )
    db.commit()

    row = (
        db.query(DBTeamSpendPeriod)
        .filter(
            DBTeamSpendPeriod.team_id == test_team.id,
            DBTeamSpendPeriod.region_id == test_region.id,
            DBTeamSpendPeriod.period_start == period_start,
            DBTeamSpendPeriod.period_end == period_end,
        )
        .first()
    )
    assert row is not None
    assert row.total_spend == 12.34
    assert row.subscription_remaining_cents == 120
    assert row.topup_remaining_cents == 30
    assert row.desired_remaining_cents == 150

    keys = (
        db.query(DBTeamSpendPeriodKey)
        .filter(DBTeamSpendPeriodKey.team_spend_period_id == row.id)
        .all()
    )
    assert len(keys) == 2


def test_upsert_team_spend_period_keeps_original_snapshot_for_same_window(
    db, test_team, test_region
):
    period_start = datetime(2026, 4, 1, tzinfo=UTC)
    period_end = datetime(2026, 5, 1, tzinfo=UTC)

    snapshot1 = {"total_spend": 5.0, "keys": []}
    snapshot2 = {"total_spend": 7.0, "keys": []}

    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot1,
    )
    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot2,
    )
    db.commit()

    rows = (
        db.query(DBTeamSpendPeriod)
        .filter(
            DBTeamSpendPeriod.team_id == test_team.id,
            DBTeamSpendPeriod.region_id == test_region.id,
            DBTeamSpendPeriod.period_start == period_start,
            DBTeamSpendPeriod.period_end == period_end,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].total_spend == 5.0


def _topup(db, team, region, *, amount_cents, days_ago):
    now = datetime.now(UTC)
    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team.id,
        region_id=region.id,
        entry_type="topup",
        amount_cents=amount_cents,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(days=days_ago),
        expires_at=now + timedelta(days=settings.POOL_PURCHASE_EXPIRY_DAYS - days_ago),
    )
    db.add(entry)
    db.commit()
    return entry


def test_pool_topup_window_opens_at_the_oldest_valid_purchase(
    db, test_team, test_region
):
    """The window spans the life of the credit the team still holds.

    It opens at the oldest still-valid purchase because the alert budget adds the
    ``consumed_cents`` of every valid entry back, which a top-up settlement
    writes for a team with no subscription. Opening at the newest purchase would
    leave spend against the older entries counted nowhere: missing from the
    numerator and never deducted from the budget.

    It closes 365 days after the NEWEST purchase, since that is when the last of
    the credit lapses.
    """
    test_team.budget_type = BudgetType.POOL
    db.commit()
    now = datetime.now(UTC)
    _topup(db, test_team, test_region, amount_cents=10_000, days_ago=100)
    _topup(db, test_team, test_region, amount_cents=10_000, days_ago=10)

    window = resolve_team_period_window(db, test_team, test_region.id, now=now)

    assert window.source == "pool_topup"
    assert 99 <= (now - window.period_start).days <= 101
    expected_end = (
        now - timedelta(days=10) + timedelta(days=settings.POOL_PURCHASE_EXPIRY_DAYS)
    )
    assert abs((window.period_end - expected_end).total_seconds()) < 5
    assert window.budget_duration == f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d"


def test_pool_topup_window_is_unchanged_for_a_single_purchase(
    db, test_team, test_region
):
    """Most top-up team+regions hold one entry: nothing moves for them."""
    test_team.budget_type = BudgetType.POOL
    db.commit()
    now = datetime.now(UTC)
    _topup(db, test_team, test_region, amount_cents=10_000, days_ago=30)

    window = resolve_team_period_window(db, test_team, test_region.id, now=now)

    # Oldest and newest are the same row, so both ends derive from it.
    assert 29 <= (now - window.period_start).days <= 31
    expected_end = (
        now - timedelta(days=30) + timedelta(days=settings.POOL_PURCHASE_EXPIRY_DAYS)
    )
    assert abs((window.period_end - expected_end).total_seconds()) < 5


def test_pool_window_ignores_topups_when_a_subscription_is_active(
    db, test_team, test_region
):
    """The subscription branch wins, so top-up dates do not touch the window."""
    test_team.budget_type = BudgetType.POOL
    db.commit()
    now = datetime.now(UTC)
    _topup(db, test_team, test_region, amount_cents=10_000, days_ago=100)
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            amount_cents=10_000,
            consumed_cents=0,
            is_active=True,
            purchased_at=now - timedelta(days=11),
            effective_period_start=now - timedelta(days=11),
            effective_period_end=now + timedelta(days=20),
        )
    )
    db.commit()

    window = resolve_team_period_window(db, test_team, test_region.id, now=now)

    assert window.source == "subscription_ledger"
    assert 10 <= (now - window.period_start).days <= 12
    assert window.budget_duration == "31d"


def test_canonical_budget_duration_maps_word_forms():
    """
    GIVEN: A budget duration written as a word
    WHEN: It is canonicalised
    THEN: The canonical equivalent is returned
    """
    assert canonical_budget_duration("hourly") == "1h"
    assert canonical_budget_duration("daily") == "24h"
    assert canonical_budget_duration("weekly") == "7d"
    assert canonical_budget_duration("monthly") == "30d"


def test_canonical_budget_duration_is_case_and_space_insensitive():
    assert canonical_budget_duration(" Monthly ") == "30d"
    assert canonical_budget_duration("WEEKLY") == "7d"


def test_canonical_budget_duration_passes_through_unrecognised():
    """LiteLLM stays the authority on what is valid, so nothing else is rewritten."""
    for value in ("30d", "7d", "24h", "1h", "1mo", "31d", "nonsense"):
        assert canonical_budget_duration(value) == value
    assert canonical_budget_duration(None) is None
    assert canonical_budget_duration("") == ""


def test_word_form_and_canonical_agree_on_period_start():
    """
    GIVEN: A word-form duration and its canonical equivalent
    WHEN: The period start is computed for each
    THEN: Both yield the same value, and the word form is no longer None
    """
    reset_at = datetime(2026, 9, 1, tzinfo=UTC)
    for word, canonical in (
        ("monthly", "30d"),
        ("weekly", "7d"),
        ("daily", "24h"),
        ("hourly", "1h"),
    ):
        via_word = compute_period_start(reset_at, canonical_budget_duration(word))
        via_canonical = compute_period_start(reset_at, canonical)
        assert via_word == via_canonical
        assert via_word is not None

        # Read-side safety net: keys already carrying a word form resolve too,
        # so no data migration is needed.
        assert compute_period_start(reset_at, word) == via_canonical


def _spend_snapshot(db, team, region, *, total_spend, days_ago):
    start = datetime.now(UTC) - timedelta(days=days_ago)
    upsert_team_spend_period(
        db=db,
        team=team,
        region_id=region.id,
        period_start=start,
        period_end=start,
        source="topup_settlement",
        snapshot={"total_spend": total_spend},
    )
    db.commit()


def _logs_service(spend=0.0):
    service = MagicMock()
    service.get_team_spend_in_range = AsyncMock(return_value=spend)
    return service


async def _settle(db, team, region, service, *, spend):
    await settle_topup_spend(
        db,
        team=team,
        region_id=region.id,
        service=service,
        lite_team_id="lite-team",
        team_info={"spend": spend},
    )
    db.commit()


@pytest.mark.asyncio
async def test_settle_topup_spend_first_settlement_charges_nothing(
    db, test_team, test_region
):
    """Spend before the first ledger entry is not charged to purchased credit."""
    test_team.budget_type = BudgetType.POOL
    db.commit()
    entry = _topup(db, test_team, test_region, amount_cents=5000, days_ago=0)
    service = _logs_service()

    await _settle(db, test_team, test_region, service, spend=12.0)

    db.refresh(entry)
    assert entry.consumed_cents == 0
    rows = db.query(DBTeamSpendPeriod).filter_by(team_id=test_team.id).all()
    assert [(r.source, r.total_spend) for r in rows] == [("topup_settlement", 12.0)]
    service.get_team_spend_in_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_settle_topup_spend_charges_oldest_entry_first(
    db, test_team, test_region
):
    test_team.budget_type = BudgetType.POOL
    db.commit()
    older = _topup(db, test_team, test_region, amount_cents=5000, days_ago=20)
    newer = _topup(db, test_team, test_region, amount_cents=5000, days_ago=10)
    _spend_snapshot(db, test_team, test_region, total_spend=10.0, days_ago=5)

    await _settle(db, test_team, test_region, _logs_service(), spend=80.0)

    db.refresh(older)
    db.refresh(newer)
    assert older.consumed_cents == 5000
    assert older.is_active is False
    assert newer.consumed_cents == 2000


@pytest.mark.asyncio
async def test_settle_topup_spend_team_without_snapshot_is_settled_from_now_on(
    db, test_team, test_region
):
    """Earlier top-ups and no snapshot: the first settlement charges $0, then the
    next one charges only the spend since that snapshot."""
    test_team.budget_type = BudgetType.POOL
    db.commit()
    oldest = _topup(db, test_team, test_region, amount_cents=5000, days_ago=30)
    _topup(db, test_team, test_region, amount_cents=5000, days_ago=10)
    service = _logs_service()

    await _settle(db, test_team, test_region, service, spend=99.0)

    db.refresh(oldest)
    assert oldest.consumed_cents == 0
    rows = db.query(DBTeamSpendPeriod).filter_by(team_id=test_team.id).all()
    assert [(r.source, r.total_spend) for r in rows] == [("topup_settlement", 99.0)]

    await _settle(db, test_team, test_region, service, spend=139.0)

    db.refresh(oldest)
    assert oldest.consumed_cents == 4000
    service.get_team_spend_in_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_settle_topup_spend_reads_logs_when_counter_dropped_below_baseline(
    db, test_team, test_region
):
    test_team.budget_type = BudgetType.POOL
    db.commit()
    entry = _topup(db, test_team, test_region, amount_cents=5000, days_ago=10)
    _spend_snapshot(db, test_team, test_region, total_spend=50.0, days_ago=5)
    service = _logs_service(8.0)

    await _settle(db, test_team, test_region, service, spend=5.0)

    service.get_team_spend_in_range.assert_awaited_once()
    db.refresh(entry)
    assert entry.consumed_cents == 800


@pytest.mark.asyncio
async def test_settle_topup_spend_skips_a_team_without_the_purchase_gate(
    db, test_team, test_region
):
    """A purchase does not reset key spend for these teams, so they never settle."""
    entry = _topup(db, test_team, test_region, amount_cents=5000, days_ago=10)
    _spend_snapshot(db, test_team, test_region, total_spend=0.0, days_ago=5)

    await _settle(db, test_team, test_region, _logs_service(), spend=40.0)

    db.refresh(entry)
    assert entry.consumed_cents == 0
    assert db.query(DBTeamSpendPeriod).filter_by(team_id=test_team.id).count() == 1


@pytest.mark.asyncio
async def test_settle_topup_spend_runs_past_a_lapsed_subscription(
    db, test_team, test_region
):
    """A subscription past its period end does not block settlement or take the spend."""
    test_team.budget_type = BudgetType.POOL
    db.commit()
    now = datetime.now(UTC)
    entry = _topup(db, test_team, test_region, amount_cents=5000, days_ago=10)
    lapsed = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="subscription",
        amount_cents=10_000,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(days=40),
        effective_period_start=now - timedelta(days=40),
        effective_period_end=now - timedelta(days=9),
        expires_at=now - timedelta(days=9),
    )
    db.add(lapsed)
    db.commit()
    _spend_snapshot(db, test_team, test_region, total_spend=10.0, days_ago=5)

    await _settle(db, test_team, test_region, _logs_service(), spend=40.0)

    db.refresh(entry)
    db.refresh(lapsed)
    assert entry.consumed_cents == 3000
    assert lapsed.consumed_cents == 0
