from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.spend_period_service import (
    resolve_team_period_window,
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

    It opens at the oldest still-valid purchase because the budget is the full face
    value of every valid entry: ``consumed_cents`` is only written by FIFO at
    invoice close, which a team with no subscription never has. Opening at the
    newest purchase would leave spend against the older entries counted nowhere —
    missing from the numerator and never deducted from the budget.

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
    """215 of 222 top-up team+regions hold one entry: nothing moves for them."""
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
