from datetime import UTC, datetime, timedelta

from app.core.periodic_budget_ledger_service import (
    add_subscription_entry,
    add_topup_entry,
    allocate_period_spend_fifo,
    compute_active_topup_remaining,
    expire_subscription_entries,
    materialize_topup_rollovers,
)
from app.db.models import DBPeriodicBudgetLedgerEntry


def _mk_ledger_entry(
    *,
    team_id: int,
    region_id: int,
    entry_type: str,
    amount_cents: int,
    consumed_cents: int,
    purchased_at: datetime,
    expires_at: datetime | None,
) -> DBPeriodicBudgetLedgerEntry:
    return DBPeriodicBudgetLedgerEntry(
        team_id=team_id,
        region_id=region_id,
        entry_type=entry_type,
        amount_cents=amount_cents,
        consumed_cents=consumed_cents,
        purchased_at=purchased_at,
        expires_at=expires_at,
        is_active=True,
    )


def test_fifo_allocation_and_rollover_materialization(db, test_team, test_region):
    now = datetime.now(UTC)
    add_topup_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=1000,
        purchased_at=now - timedelta(days=10),
        source_payment_id=None,
        stripe_payment_id="sess_1",
    )
    add_topup_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=500,
        purchased_at=now - timedelta(days=5),
        source_payment_id=None,
        stripe_payment_id="sess_2",
    )
    db.commit()

    result = allocate_period_spend_fifo(
        db, team_id=test_team.id, region_id=test_region.id, spend_cents=1200
    )
    db.commit()
    assert result.allocated_cents == 1200
    assert result.unallocated_cents == 0

    remaining = compute_active_topup_remaining(
        db, team_id=test_team.id, region_id=test_region.id
    )
    assert remaining == 300

    rolled = materialize_topup_rollovers(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        source_invoice_id="in_123",
        rollover_at=now,
    )
    db.commit()
    assert rolled == 300

    # idempotent for same invoice
    rolled_again = materialize_topup_rollovers(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        source_invoice_id="in_123",
        rollover_at=now,
    )
    db.commit()
    assert rolled_again == 0

    # exactly one rollover row per invoice/team/region
    rows = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == test_team.id,
            DBPeriodicBudgetLedgerEntry.region_id == test_region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup_rollover",
            DBPeriodicBudgetLedgerEntry.source_invoice_id == "in_123",
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount_cents == 300


def _setup_period(db, team, region, *, sub_cents, topup_cents, stripe_payment_id):
    """
    Simulate the state at webhook time for a completed period:
      - Subscription entry whose expires_at is already in the past
        (Stripe period_end == now when the webhook fires).
      - One top-up entry added during the period (still valid for 365 d).
    Returns (period_start, period_end, sub_entry, topup_entry).
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(days=31)
    # period_end is set to exactly now to reproduce the webhook timing:
    # Stripe's period_end timestamp == the moment the webhook fires.
    period_end = now

    sub_entry = add_subscription_entry(
        db,
        team_id=team.id,
        region_id=region.id,
        amount_cents=sub_cents,
        purchased_at=period_start,
        period_start=period_start,
        period_end=period_end,
        source_payment_id=None,
        source_invoice_id=f"in_sub_{stripe_payment_id}",
    )
    topup_entry = add_topup_entry(
        db,
        team_id=team.id,
        region_id=region.id,
        amount_cents=topup_cents,
        purchased_at=period_start + timedelta(days=5),
        source_payment_id=None,
        stripe_payment_id=stripe_payment_id,
    )
    db.commit()
    return period_start, period_end, sub_entry, topup_entry


def test_example1_spend_within_stripe_budget_topup_fully_preserved(
    db, test_team, test_region
):
    """
    Spec example 1: Stripe $10, Top-up $5, Spend $7
    → spend drawn entirely from Stripe budget
    → remaining top-up $5 carries over in full
    → new-period budget = $10 + $5 = $15
    """
    period_start, period_end, sub_entry, topup_entry = _setup_period(
        db,
        test_team,
        test_region,
        sub_cents=1000,
        topup_cents=500,
        stripe_payment_id="pay_ex1",
    )

    # Webhook fires: allocate $7 spend against ledger (subscription expires_at == now)
    result = allocate_period_spend_fifo(
        db, team_id=test_team.id, region_id=test_region.id, spend_cents=700
    )
    db.commit()
    assert result.allocated_cents == 700
    assert result.unallocated_cents == 0

    # Subscription absorbed all spend; top-up untouched
    db.refresh(sub_entry)
    db.refresh(topup_entry)
    assert sub_entry.consumed_cents == 700
    assert topup_entry.consumed_cents == 0

    # Roll over remaining top-up ($5) into new period
    rolled = materialize_topup_rollovers(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        source_invoice_id="in_new1",
        rollover_at=period_end,
    )
    db.commit()
    assert rolled == 500  # full $5 rolls over

    expire_subscription_entries(
        db, team_id=test_team.id, region_id=test_region.id, period_end=period_end
    )
    db.commit()

    # Active top-up remaining = $5 → new-period budget = $10 + $5 = $15
    remaining = compute_active_topup_remaining(
        db, team_id=test_team.id, region_id=test_region.id
    )
    assert remaining == 500


def test_example2_spend_exceeds_stripe_budget_topup_partially_consumed(
    db, test_team, test_region
):
    """
    Spec example 2: Stripe $10, Top-up $5, Spend $13
    → $10 from Stripe, $3 from top-up
    → remaining top-up $2 carries over
    → new-period budget = $10 + $2 = $12
    """
    period_start, period_end, sub_entry, topup_entry = _setup_period(
        db,
        test_team,
        test_region,
        sub_cents=1000,
        topup_cents=500,
        stripe_payment_id="pay_ex2",
    )

    result = allocate_period_spend_fifo(
        db, team_id=test_team.id, region_id=test_region.id, spend_cents=1300
    )
    db.commit()
    assert result.allocated_cents == 1300
    assert result.unallocated_cents == 0

    db.refresh(sub_entry)
    db.refresh(topup_entry)
    assert sub_entry.consumed_cents == 1000  # fully consumed
    assert topup_entry.consumed_cents == 300  # only the overflow hits top-up

    rolled = materialize_topup_rollovers(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        source_invoice_id="in_new2",
        rollover_at=period_end,
    )
    db.commit()
    assert rolled == 200  # $2 remaining rolls over

    expire_subscription_entries(
        db, team_id=test_team.id, region_id=test_region.id, period_end=period_end
    )
    db.commit()

    remaining = compute_active_topup_remaining(
        db, team_id=test_team.id, region_id=test_region.id
    )
    assert remaining == 200  # $2 → new-period budget = $10 + $2 = $12


def test_rollover_preserves_non_expiring_sources(db, test_team, test_region):
    now = datetime.now(UTC)

    # Non-expiring source and finite-expiry source should produce non-expiring rollover.
    db.add(
        _mk_ledger_entry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            amount_cents=500,
            consumed_cents=100,
            purchased_at=now - timedelta(days=15),
            expires_at=None,
        )
    )
    db.add(
        _mk_ledger_entry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            amount_cents=400,
            consumed_cents=0,
            purchased_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=20),
        )
    )
    db.commit()

    rolled = materialize_topup_rollovers(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        source_invoice_id="in_non_expiring_rollover",
        rollover_at=now,
    )
    db.commit()

    assert rolled == 800
    rollover_row = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == test_team.id,
            DBPeriodicBudgetLedgerEntry.region_id == test_region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup_rollover",
            DBPeriodicBudgetLedgerEntry.source_invoice_id == "in_non_expiring_rollover",
        )
        .one()
    )
    assert rollover_row.expires_at is None
