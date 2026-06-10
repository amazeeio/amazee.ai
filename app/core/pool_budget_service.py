from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.periodic_budget_ledger_service import compute_active_topup_remaining
from app.db.models import DBPeriodicBudgetLedgerEntry, DBPoolPurchase


def _pool_budget_duration_from_last_purchase(
    db: Session, team_id: int, region_id: int
) -> str:
    latest_purchase_at = (
        db.query(func.max(DBPoolPurchase.purchased_at))
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .scalar()
    )
    if latest_purchase_at is None:
        return f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d"
    if latest_purchase_at.tzinfo is None:
        latest_purchase = latest_purchase_at.replace(tzinfo=UTC)
    else:
        latest_purchase = latest_purchase_at
    days_since_last_purchase = (datetime.now(UTC) - latest_purchase).days
    days_left = max(0, settings.POOL_PURCHASE_EXPIRY_DAYS - days_since_last_purchase)
    return f"{days_left}d"


def pool_available_budget_for_team_region(
    db: Session, team_id: int, region_id: int
) -> float:
    """Available POOL budget from active subscription + active top-ups."""
    now = datetime.now(UTC)
    sub_remaining_expr = (
        DBPeriodicBudgetLedgerEntry.amount_cents
        - DBPeriodicBudgetLedgerEntry.consumed_cents
    )
    sub_remaining_cents = (
        db.query(func.coalesce(func.sum(sub_remaining_expr), 0))
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.consumed_cents
            < DBPeriodicBudgetLedgerEntry.amount_cents,
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
        )
        .scalar()
        or 0
    )
    topup_remaining_cents = compute_active_topup_remaining(
        db, team_id=team_id, region_id=region_id
    )
    return round(
        float(int(sub_remaining_cents) + int(topup_remaining_cents)) / 100.0,
        4,
    )


def pool_team_has_ever_purchased(db: Session, team_id: int, region_id: int) -> bool:
    """Return True if the team has ever had at least one subscription cycle
    (DBPeriodicBudgetLedgerEntry) or direct pool top-up (DBPoolPurchase)
    recorded for this region, regardless of remaining balance.

    This is the sole criterion for the block/unblock decision on keys:
    - False (no purchase ever)  → key must be created/kept blocked
    - True  (at least one purchase) → key must be unblocked, even if all
      budget is consumed.  Actual spend enforcement is handled by LiteLLM
      max_budget, not the blocked flag.
    """
    has_ledger_entry = (
        db.query(DBPeriodicBudgetLedgerEntry.id)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
        )
        .first()
        is not None
    )
    if has_ledger_entry:
        return True
    has_pool_purchase = (
        db.query(DBPoolPurchase.id)
        .filter(
            DBPoolPurchase.team_id == team_id,
            DBPoolPurchase.region_id == region_id,
        )
        .first()
        is not None
    )
    return has_pool_purchase


def pool_team_budget_duration_for_enforcement(
    db: Session, team_id: int, region_id: int
) -> str:
    """
    POOL team budget duration for enforcement:
    - Use 31d when an active subscription cycle window exists.
    - Otherwise fall back to top-up purchase-window semantics.
    """
    now = datetime.now(UTC)
    active_subscription = (
        db.query(DBPeriodicBudgetLedgerEntry.id)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.effective_period_start.isnot(None),
            DBPeriodicBudgetLedgerEntry.effective_period_end.isnot(None),
            DBPeriodicBudgetLedgerEntry.effective_period_end > now,
        )
        .first()
    )
    if active_subscription is not None:
        return "31d"
    return _pool_budget_duration_from_last_purchase(db, team_id, region_id)
