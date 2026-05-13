from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy.orm import Session

from app.db.models import DBPeriodicBudgetLedgerEntry


TOPUP_EXPIRY_DAYS = 365


@dataclass
class AllocationResult:
    allocated_cents: int
    unallocated_cents: int


def _active_entries(db: Session, team_id: int, region_id: int) -> list[DBPeriodicBudgetLedgerEntry]:
    now = datetime.now(UTC)
    return (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (DBPeriodicBudgetLedgerEntry.expires_at.is_(None) | (DBPeriodicBudgetLedgerEntry.expires_at > now)),
            DBPeriodicBudgetLedgerEntry.consumed_cents < DBPeriodicBudgetLedgerEntry.amount_cents,
        )
        .order_by(DBPeriodicBudgetLedgerEntry.purchased_at.asc(), DBPeriodicBudgetLedgerEntry.id.asc())
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
        expires_at=purchased_at + timedelta(days=TOPUP_EXPIRY_DAYS),
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


def expire_subscription_entries(db: Session, *, team_id: int, region_id: int, period_end: datetime) -> None:
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
            (DBPeriodicBudgetLedgerEntry.expires_at.is_(None) | (DBPeriodicBudgetLedgerEntry.expires_at > now)),
        )
        .all()
    )
    return sum(max(0, e.amount_cents - e.consumed_cents) for e in entries)
