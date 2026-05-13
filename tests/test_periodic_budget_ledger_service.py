from datetime import UTC, datetime, timedelta

from app.core.periodic_budget_ledger_service import (
    add_topup_entry,
    allocate_period_spend_fifo,
    compute_active_topup_remaining,
    materialize_topup_rollovers,
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
