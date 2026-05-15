from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DBPeriodicBudgetLedgerEntry


@dataclass
class AllocationResult:
    allocated_cents: int
    unallocated_cents: int


@dataclass
class BudgetDriftResult:
    expected_max_budget: float
    actual_max_budget: float
    drift: float


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
) -> DBPeriodicBudgetLedgerEntry:
    if source_invoice_id:
        existing = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team_id,
                DBPeriodicBudgetLedgerEntry.region_id == region_id,
                DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
                DBPeriodicBudgetLedgerEntry.source_invoice_id == source_invoice_id,
            )
            .first()
        )
        if existing:
            return existing

    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team_id,
        region_id=region_id,
        entry_type="subscription",
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
    existing = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == stripe_payment_id,
        )
        .first()
    )
    if existing:
        return None

    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team_id,
        region_id=region_id,
        entry_type="topup",
        source_payment_id=source_payment_id,
        stripe_payment_id=stripe_payment_id,
        amount_cents=amount_cents,
        consumed_cents=0,
        purchased_at=purchased_at,
        expires_at=purchased_at + timedelta(days=settings.PERIODIC_TOPUP_EXPIRY_DAYS),
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
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
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
    entries = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type.in_(["topup", "topup_rollover"]),
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
        )
        .all()
    )
    return sum(max(0, e.amount_cents - e.consumed_cents) for e in entries)


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
                DBPeriodicBudgetLedgerEntry.entry_type == "topup_rollover",
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
            DBPeriodicBudgetLedgerEntry.entry_type.in_(["topup", "topup_rollover"]),
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
                DBPeriodicBudgetLedgerEntry.entry_type == "topup_rollover",
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
                entry_type="topup_rollover",
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
