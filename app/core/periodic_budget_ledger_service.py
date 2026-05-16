from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DBPeriodicBudgetLedgerEntry

ENTRY_TYPE_SUBSCRIPTION = "subscription"
ENTRY_TYPE_TOPUP = "topup"
ENTRY_TYPE_TOPUP_ROLLOVER = "topup_rollover"


@dataclass
class AllocationResult:
    allocated_cents: int
    unallocated_cents: int


@dataclass
class BudgetDriftResult:
    expected_max_budget_cents: int
    actual_max_budget_cents: int
    drift_cents: int


def _active_entries(
    db: Session, team_id: int, region_id: int
) -> list[DBPeriodicBudgetLedgerEntry]:
    now = datetime.now(UTC)
    return (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
            DBPeriodicBudgetLedgerEntry.consumed_cents
            < DBPeriodicBudgetLedgerEntry.amount_cents,
        )
        .with_for_update()
        .order_by(
            DBPeriodicBudgetLedgerEntry.purchased_at.asc(),
            DBPeriodicBudgetLedgerEntry.id.asc(),
        )
        .all()
    )


def add_subscription_entry(
    db: Session,
    *,
    team_id: int,
    region_id: int,
    amount_cents: int,
    purchased_at: datetime,
    period_start: datetime,
    period_end: datetime,
    source_payment_id: int | None,
    source_invoice_id: str | None,
) -> DBPeriodicBudgetLedgerEntry | None:
    if amount_cents <= 0:
        return None
    if source_invoice_id:
        existing = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team_id,
                DBPeriodicBudgetLedgerEntry.region_id == region_id,
                DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_SUBSCRIPTION,
                DBPeriodicBudgetLedgerEntry.source_invoice_id == source_invoice_id,
            )
            .first()
        )
        if existing:
            return existing

    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team_id,
        region_id=region_id,
        entry_type=ENTRY_TYPE_SUBSCRIPTION,
        source_payment_id=source_payment_id,
        source_invoice_id=source_invoice_id,
        amount_cents=amount_cents,
        consumed_cents=0,
        purchased_at=purchased_at,
        effective_period_start=period_start,
        effective_period_end=period_end,
        expires_at=period_end,
        is_active=True,
    )
    db.add(entry)
    db.flush()
    return entry


def add_topup_entry(
    db: Session,
    *,
    team_id: int,
    region_id: int,
    amount_cents: int,
    purchased_at: datetime,
    source_payment_id: int | None,
    stripe_payment_id: str,
) -> DBPeriodicBudgetLedgerEntry | None:
    if amount_cents <= 0:
        return None
    existing = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_TOPUP,
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == stripe_payment_id,
        )
        .first()
    )
    if existing:
        return None

    # For periodic teams, top-up expiry should be anchored to the latest
    # top-up date in this team/region so consecutive top-ups extend from
    # the most recent top-up window.
    last_topup = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_TOPUP,
        )
        .order_by(
            DBPeriodicBudgetLedgerEntry.purchased_at.desc(),
            DBPeriodicBudgetLedgerEntry.id.desc(),
        )
        .first()
    )
    expiry_anchor = purchased_at
    if (
        last_topup
        and last_topup.purchased_at
        and last_topup.purchased_at > expiry_anchor
    ):
        expiry_anchor = last_topup.purchased_at

    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team_id,
        region_id=region_id,
        entry_type=ENTRY_TYPE_TOPUP,
        source_payment_id=source_payment_id,
        stripe_payment_id=stripe_payment_id,
        amount_cents=amount_cents,
        consumed_cents=0,
        purchased_at=purchased_at,
        expires_at=expiry_anchor + timedelta(days=settings.PERIODIC_TOPUP_EXPIRY_DAYS),
        is_active=True,
    )
    db.add(entry)
    db.flush()
    return entry


def allocate_period_spend_fifo(
    db: Session, *, team_id: int, region_id: int, spend_cents: int
) -> AllocationResult:
    remaining = max(0, int(spend_cents))
    allocated = 0
    for entry in _active_entries(db, team_id, region_id):
        if remaining <= 0:
            break
        free = max(0, entry.amount_cents - entry.consumed_cents)
        if free <= 0:
            continue
        delta = min(free, remaining)
        entry.consumed_cents += delta
        if entry.consumed_cents >= entry.amount_cents:
            entry.is_active = False
        allocated += delta
        remaining -= delta
    db.flush()
    return AllocationResult(allocated_cents=allocated, unallocated_cents=remaining)


def expire_subscription_entries(
    db: Session, *, team_id: int, region_id: int, period_end: datetime
) -> None:
    entries = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_SUBSCRIPTION,
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.effective_period_end <= period_end,
        )
        .all()
    )
    for entry in entries:
        entry.is_active = False
    db.flush()


def compute_active_topup_remaining(db: Session, *, team_id: int, region_id: int) -> int:
    now = datetime.now(UTC)
    remaining_cents_expr = (
        DBPeriodicBudgetLedgerEntry.amount_cents
        - DBPeriodicBudgetLedgerEntry.consumed_cents
    )
    total = (
        db.query(func.coalesce(func.sum(remaining_cents_expr), 0))
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type.in_(
                [ENTRY_TYPE_TOPUP, ENTRY_TYPE_TOPUP_ROLLOVER]
            ),
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.consumed_cents
            < DBPeriodicBudgetLedgerEntry.amount_cents,
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
        )
        .scalar()
    )
    return int(total or 0)


def materialize_topup_rollovers(
    db: Session,
    *,
    team_id: int,
    region_id: int,
    source_invoice_id: str | None,
    rollover_at: datetime,
) -> int:
    if source_invoice_id:
        existing_for_invoice = (
            db.query(DBPeriodicBudgetLedgerEntry.id)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team_id,
                DBPeriodicBudgetLedgerEntry.region_id == region_id,
                DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_TOPUP_ROLLOVER,
                DBPeriodicBudgetLedgerEntry.source_invoice_id == source_invoice_id,
            )
            .first()
        )
        if existing_for_invoice:
            return 0

    now = datetime.now(UTC)
    rollover_total = 0
    source_entries = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type.in_(
                [ENTRY_TYPE_TOPUP, ENTRY_TYPE_TOPUP_ROLLOVER]
            ),
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
        )
        .all()
    )
    for entry in source_entries:
        remaining = max(0, entry.amount_cents - entry.consumed_cents)
        if remaining <= 0:
            entry.is_active = False
            continue
        existing = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.entry_type == ENTRY_TYPE_TOPUP_ROLLOVER,
                DBPeriodicBudgetLedgerEntry.rolled_over_from_id == entry.id,
                DBPeriodicBudgetLedgerEntry.source_invoice_id == source_invoice_id,
            )
            .first()
        )
        if existing:
            entry.is_active = False
            continue
        db.add(
            DBPeriodicBudgetLedgerEntry(
                team_id=team_id,
                region_id=region_id,
                entry_type=ENTRY_TYPE_TOPUP_ROLLOVER,
                source_invoice_id=source_invoice_id,
                amount_cents=remaining,
                consumed_cents=0,
                purchased_at=rollover_at,
                expires_at=entry.expires_at,
                rolled_over_from_id=entry.id,
                is_active=True,
            )
        )
        entry.is_active = False
        rollover_total += remaining
    db.flush()
    return rollover_total
