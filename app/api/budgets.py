import logging
import asyncio
from datetime import UTC, date, datetime

from app.core.config import settings
from app.core.limit_service import LimitService
from app.core.security import get_role_min_system_admin
from app.core.team_service import (
    get_team_region_litellm_keys,
    propagate_team_budget_to_keys,
)
from app.db.database import get_db
from app.db.models import (
    DBLimitedResource,
    DBPeriodicPayment,
    DBPoolPurchase,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBTeamRegion,
    DBPeriodicBudgetLedgerEntry,
)
from app.schemas.limits import LimitSource, LimitType, OwnerType, ResourceType, UnitType
from app.schemas.models import (
    BudgetType,
    PoolPurchaseHistoryResponse,
    PoolPurchaseRequest,
    PoolPurchaseResponse,
    PoolRegionPurchaseHistoryResponse,
    PeriodicBudgetStatusResponse,
    PeriodicTopupRequest,
    PeriodicTopupResponse,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.services.litellm import LiteLLMService
from app.core.spend_period_service import (
    fetch_team_spend_snapshot_for_region,
    upsert_team_spend_period,
)
from app.core.periodic_budget_ledger_service import (
    add_topup_entry,
    compute_active_topup_remaining,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["budgets"])
MONTHLY_BUDGET_DURATION = "1mo"


@router.get(
    "/region/{region_id}/teams/{team_id}/periodic-status",
    response_model=PeriodicBudgetStatusResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_periodic_budget_status(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.budget_type != BudgetType.PERIODIC:
        raise HTTPException(
            status_code=400, detail="Endpoint is only valid for PERIODIC teams"
        )
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    sub_remaining = 0
    subscription_period_end = None
    active_subscriptions = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
        )
        .all()
    )
    for row in active_subscriptions:
        sub_remaining += max(0, row.amount_cents - row.consumed_cents)
        if row.effective_period_end and (
            subscription_period_end is None
            or row.effective_period_end > subscription_period_end
        ):
            subscription_period_end = row.effective_period_end

    nearest_topup_expiry = (
        db.query(func.min(DBPeriodicBudgetLedgerEntry.expires_at))
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type.in_(["topup", "topup_rollover"]),
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.expires_at.isnot(None),
        )
        .scalar()
    )
    topup_remaining = compute_active_topup_remaining(
        db, team_id=team_id, region_id=region_id
    )
    return PeriodicBudgetStatusResponse(
        team_id=team_id,
        region_id=region_id,
        subscription_remaining_cents=sub_remaining,
        topup_remaining_cents=topup_remaining,
        desired_remaining_cents=sub_remaining + topup_remaining,
        subscription_period_end=subscription_period_end,
        nearest_topup_expiry=nearest_topup_expiry,
    )


def _get_operator_manual_team_budget_limit(db: Session, team_id: int) -> float | None:
    """Return manual team budget cap set by an operator (not purchase automation)."""
    existing_limit = (
        db.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == team_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
            DBLimitedResource.limited_by == LimitSource.MANUAL,
        )
        .first()
    )
    if (
        not existing_limit
        or existing_limit.max_value is None
        or existing_limit.set_by == "pool_purchase"
    ):
        return None
    return float(existing_limit.max_value)


def _current_month_anchor() -> date:
    now = datetime.now(UTC)
    return date(year=now.year, month=now.month, day=1)


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
        return f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d"
    if latest_purchase_at.tzinfo is None:
        latest_purchase = latest_purchase_at.replace(tzinfo=UTC)
    else:
        latest_purchase = latest_purchase_at
    days_since_last_purchase = (datetime.now(UTC) - latest_purchase).days
    days_left = max(0, settings.POOL_BUDGET_EXPIRATION_DAYS - days_since_last_purchase)
    return f"{days_left}d"


def _compute_pool_monthly_effective_budget(
    purchased_total: float,
    month_start_spend: float,
    monthly_cap: float,
) -> float:
    return round(
        min(float(purchased_total), float(month_start_spend) + float(monthly_cap)), 4
    )


async def _sync_pool_key_effective_budgets(
    db: Session, *, team_id: int, region: DBRegion, purchased_total: float
) -> list[str]:
    """
    Keep key-level effective limits coherent for POOL teams.

    - purchased_total <= 0: hard-lock all team keys (max_budget=0)
    - purchased_total > 0: restore key max_budget from spend_caps (or clear)
    """
    keys = get_team_region_litellm_keys(
        db,
        team_id=team_id,
        region_id=region.id,
    )
    if not keys:
        return []

    key_caps = (
        db.query(DBSpendCap.key_id, DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == region.id,
            DBSpendCap.key_id.isnot(None),
            DBSpendCap.max_budget.isnot(None),
            DBSpendCap.key_id.in_([k.id for k in keys]),
        )
        .all()
    )
    cap_map = {int(key_id): float(max_budget) for key_id, max_budget in key_caps}
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    semaphore = asyncio.Semaphore(10)

    async def _sync_key_budget(key: DBPrivateAIKey) -> str | None:
        try:
            async with semaphore:
                if purchased_total <= 0:
                    await service.update_key_budget(
                        litellm_token=key.litellm_token,
                        budget_duration=MONTHLY_BUDGET_DURATION,
                        max_budget=0.0,
                        clear_max_budget=False,
                    )
                else:
                    configured_cap = cap_map.get(key.id)
                    if configured_cap is None:
                        # No user-defined key cap — clear both max_budget
                        # and budget_duration so no stale duration remains.
                        await service.update_key_budget(
                            litellm_token=key.litellm_token,
                            budget_duration=None,
                            max_budget=None,
                            clear_max_budget=True,
                            clear_budget_duration=True,
                        )
                    else:
                        await service.update_key_budget(
                            litellm_token=key.litellm_token,
                            budget_duration=MONTHLY_BUDGET_DURATION,
                            max_budget=configured_cap,
                            clear_max_budget=False,
                        )
            return None
        except Exception as exc:
            return f"Key {key.id}: {str(exc)}"

    return [
        error
        for error in await asyncio.gather(*[_sync_key_budget(key) for key in keys])
        if error is not None
    ]


@router.post(
    "/region/{region_id}/teams/{team_id}/purchase",
    response_model=PoolPurchaseResponse | PeriodicTopupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def purchase_team_budget(
    region_id: int,
    team_id: int,
    purchase: PoolPurchaseRequest | PeriodicTopupRequest,
    db: Session = Depends(get_db),
):
    """
    Record a pool budget purchase for a team and update their LiteLLM team budget.

    Only works for teams that require purchase-gated POOL requests.
    Handles concurrent purchases by checking for duplicate stripe_payment_id.
    POOL updates are team-budget only; per-key max_budget remains unset.
    """
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    if not team.requires_pool_purchase_gate:
        if team.budget_type == BudgetType.PERIODIC:
            return await purchase_periodic_topup(
                region_id=region_id,
                team=team,
                purchase=purchase,
                db=db,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This endpoint only works for teams that require purchase-gated requests "
                "(pool budget type)"
            ),
        )

    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    existing_purchase = (
        db.query(DBPoolPurchase)
        .filter(DBPoolPurchase.stripe_payment_id == purchase.stripe_payment_id)
        .first()
    )

    if existing_purchase:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    # Compute the closing POOL period window using the state BEFORE the new
    # purchase is recorded (latest_purchase_at must be queried before the insert).
    latest_purchase_at = (
        db.query(func.max(DBPoolPurchase.purchased_at))
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .scalar()
    )
    period_start = latest_purchase_at or team.created_at or purchase.purchased_at
    period_end = purchase.purchased_at

    # Insert the purchase record and flush BEFORE any external calls so that
    # stripe_payment_id uniqueness is enforced in the DB before side effects.
    # Concurrent duplicate requests will fail here rather than wasting an
    # external LiteLLM round-trip.
    purchase_record = DBPoolPurchase(
        team_id=team_id,
        region_id=region_id,
        amount_cents=purchase.amount_cents,
        currency=purchase.currency,
        purchased_at=purchase.purchased_at,
        stripe_payment_id=purchase.stripe_payment_id,
        created_at=datetime.now(UTC),
    )
    db.add(purchase_record)

    team.last_pool_purchase = purchase.purchased_at

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    # Capture spend for the closing POOL period BEFORE mutating budget state.
    # Uses the pre-computed period window (before the insert above).
    if period_start and period_end and period_end > period_start:
        try:
            snapshot = await fetch_team_spend_snapshot_for_region(
                db=db,
                team=team,
                region=region,
            )
            upsert_team_spend_period(
                db=db,
                team=team,
                region_id=region_id,
                period_start=period_start,
                period_end=period_end,
                source="pool_purchase_litellm_sync",
                snapshot=snapshot,
                stripe_invoice_id=purchase.stripe_payment_id,
                raw_payload={
                    "trigger": "pool_purchase",
                    "amount_cents": purchase.amount_cents,
                    "currency": purchase.currency,
                },
            )
        except Exception as exc:
            logger.error(
                "Failed to capture POOL spend period for team_id=%s region_id=%s before purchase: %s",
                team_id,
                region_id,
                str(exc),
            )
            # Do not block purchases if snapshot capture fails (e.g. transient
            # LiteLLM DNS/network issues in tests or degraded environments).
            # Budget update/purchase recording remains the primary operation.

    amount_dollars = purchase.amount_cents / 100.0
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    previous_team_info: dict | None = None
    previous_max_budget = None
    previous_budget_duration = None
    try:
        previous_team_info_resp = await service.get_team_info(lite_team_id)
        previous_team_info = previous_team_info_resp.get(
            "team_info", previous_team_info_resp
        )
        previous_max_budget = previous_team_info.get("max_budget")
        previous_budget_duration = previous_team_info.get("budget_duration")
    except Exception as exc:
        logger.warning(
            "Unable to snapshot existing LiteLLM team budget for rollback (team_id=%s region_id=%s): %s",
            team_id,
            region_id,
            str(exc),
        )

    total_purchased_cents = (
        db.query(func.sum(DBPoolPurchase.amount_cents))
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .scalar()
        or 0
    )
    total_purchased_dollars = total_purchased_cents / 100.0

    # LiteLLM's max_budget is a ceiling: requests are rejected when
    # spend >= max_budget.  Spend is tracked independently by LiteLLM and
    # never subtracted from max_budget.  Therefore the correct value is the
    # cumulative total of all purchases — NOT "purchases minus spend".
    new_total_budget = total_purchased_dollars
    operator_manual_cap = _get_operator_manual_team_budget_limit(db, team_id)
    effective_team_budget = (
        min(operator_manual_cap, new_total_budget)
        if operator_manual_cap is not None
        else new_total_budget
    )

    monthly_cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.team_id == team_id,
            DBSpendCap.region_id == region_id,
            DBSpendCap.budget_duration == MONTHLY_BUDGET_DURATION,
            DBSpendCap.max_budget.isnot(None),
        )
        .first()
    )
    if monthly_cap is not None:
        if monthly_cap.month_start_spend is None:
            if previous_team_info is None:
                previous_team_info_resp = await service.get_team_info(lite_team_id)
                previous_team_info = previous_team_info_resp.get(
                    "team_info", previous_team_info_resp
                )
            monthly_cap.month_start_spend = round(
                float(previous_team_info.get("spend", 0.0) or 0.0), 4
            )
            monthly_cap.month_anchor = _current_month_anchor()
            db.add(monthly_cap)
            db.flush()
        effective_team_budget = min(
            effective_team_budget,
            _compute_pool_monthly_effective_budget(
                purchased_total=new_total_budget,
                month_start_spend=float(monthly_cap.month_start_spend or 0.0),
                monthly_cap=float(monthly_cap.max_budget or 0.0),
            ),
        )

    # Persist purchase-managed team budget only when there is no operator
    # manual cap. Operator caps are intentionally preserved.
    limit_service = LimitService(db)
    if operator_manual_cap is None:
        limit_service.set_limit(
            owner_type=OwnerType.TEAM,
            owner_id=team_id,
            resource_type=ResourceType.BUDGET,
            limit_type=LimitType.DATA_PLANE,
            unit=UnitType.DOLLAR,
            max_value=new_total_budget,
            current_value=None,
            limited_by=LimitSource.MANUAL,
            set_by="pool_purchase",
            commit=False,
            trigger_propagation=False,
        )

    try:
        propagation_result = await propagate_team_budget_to_keys(
            db,
            team_id,
            effective_team_budget,
            _pool_budget_duration_from_last_purchase(
                db=db, team_id=team_id, region_id=region_id
            ),
            region_id=region_id,
            update_key_limits=False,
            apply_to_keys=False,
        )
        if isinstance(propagation_result, dict) and propagation_result.get("errors"):
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Failed to update team budget in LiteLLM: "
                    + "; ".join(propagation_result["errors"])
                ),
            )
        key_sync_errors = await _sync_pool_key_effective_budgets(
            db,
            team_id=team_id,
            region=region,
            purchased_total=new_total_budget,
        )
        if key_sync_errors:
            db.rollback()
            rollback_error: str | None = None
            if previous_team_info is not None:
                try:
                    await service.update_team_budget(
                        team_id=lite_team_id,
                        max_budget=previous_max_budget,
                        budget_duration=previous_budget_duration,
                    )
                except Exception as exc:
                    rollback_error = str(exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Failed to update key budgets in LiteLLM after purchase: "
                    + "; ".join(key_sync_errors)
                    + (
                        (
                            ". Also failed to restore previous LiteLLM team budget; "
                            "manual reconciliation is required: " + rollback_error
                        )
                        if rollback_error
                        else ""
                    )
                    + (
                        ". Previous LiteLLM team budget snapshot was unavailable; manual reconciliation may be required."
                        if previous_team_info is None
                        else ""
                    )
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to propagate budget to keys: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update team budget in LiteLLM: {str(e)}",
        )

    try:
        db.commit()
        db.refresh(purchase_record)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    logger.info(
        f"Pool purchase recorded for team {team_id}: "
        f"${amount_dollars:.2f} added, purchased total: ${new_total_budget:.2f}, "
        f"effective team budget: ${effective_team_budget:.2f}, "
        f"operator manual cap: "
        f"{f'${operator_manual_cap:.2f}' if operator_manual_cap is not None else 'none'}"
    )

    return PoolPurchaseResponse(
        id=purchase_record.id,
        team_id=team_id,
        region_id=region_id,
        amount_cents=purchase_record.amount_cents,
        currency=purchase_record.currency,
        purchased_at=purchase_record.purchased_at,
        stripe_payment_id=purchase_record.stripe_payment_id,
        created_at=purchase_record.created_at,
        new_total_budget_cents=int(new_total_budget * 100),
        keys_updated=0,
    )


async def purchase_periodic_topup(
    *, region_id: int, team: DBTeam, purchase: PoolPurchaseRequest | PeriodicTopupRequest, db: Session
) -> PeriodicTopupResponse:
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )
    team_region = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.team_id == team.id, DBTeamRegion.region_id == region_id)
        .first()
    )
    if team_region is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Region is not assigned to this team",
        )

    existing_topup = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == purchase.stripe_payment_id,
        )
        .first()
    )
    if existing_topup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    payment_record = DBPeriodicPayment(
        team_id=team.id,
        stripe_payment_id=purchase.stripe_payment_id,
        amount_cents=purchase.amount_cents,
        currency=purchase.currency.lower(),
        payment_type="topup",
        status="completed",
        sync_status="pending",
        payment_date=purchase.purchased_at,
    )
    db.add(payment_record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    topup_entry = add_topup_entry(
        db,
        team_id=team.id,
        region_id=region_id,
        amount_cents=purchase.amount_cents,
        purchased_at=purchase.purchased_at,
        source_payment_id=payment_record.id,
        stripe_payment_id=purchase.stripe_payment_id,
    )
    if topup_entry is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
    team_info_resp = await service.get_team_info(lite_team_id)
    team_info = team_info_resp.get("team_info", team_info_resp)
    current_spend = float(team_info.get("spend", 0.0) or 0.0)

    sub_remaining_cents = 0
    active_subscriptions = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
        )
        .all()
    )
    for row in active_subscriptions:
        sub_remaining_cents += max(0, row.amount_cents - row.consumed_cents)

    topup_remaining_cents = compute_active_topup_remaining(
        db, team_id=team.id, region_id=region_id
    )
    desired_remaining = (sub_remaining_cents + topup_remaining_cents) / 100.0
    new_total_budget = round(current_spend + desired_remaining, 4)

    await service.update_team_budget(
        team_id=lite_team_id,
        max_budget=new_total_budget,
        budget_duration=MONTHLY_BUDGET_DURATION,
    )
    db.commit()
    db.refresh(topup_entry)

    return PeriodicTopupResponse(
        id=topup_entry.id,
        team_id=team.id,
        region_id=region_id,
        amount_cents=topup_entry.amount_cents,
        currency=purchase.currency,
        purchased_at=topup_entry.purchased_at,
        stripe_payment_id=topup_entry.stripe_payment_id or purchase.stripe_payment_id,
        created_at=topup_entry.created_at,
        new_total_budget_cents=int(new_total_budget * 100),
        keys_updated=0,
    )


@router.get(
    "/region/{region_id}/teams/{team_id}/purchase-history",
    response_model=PoolPurchaseHistoryResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_purchase_history(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    """Get purchase history for a team in a region."""
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    purchases = (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .order_by(DBPoolPurchase.purchased_at.desc())
        .all()
    )

    return PoolPurchaseHistoryResponse(
        team_id=team_id, region_id=region_id, purchases=purchases
    )


@router.get(
    "/region/{region_id}/purchase-history",
    response_model=PoolRegionPurchaseHistoryResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_region_purchase_history(region_id: int, db: Session = Depends(get_db)):
    """Get purchase history for a region across all teams."""
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    purchases = (
        db.query(DBPoolPurchase)
        .filter(DBPoolPurchase.region_id == region_id)
        .order_by(DBPoolPurchase.purchased_at.desc())
        .all()
    )

    return PoolRegionPurchaseHistoryResponse(region_id=region_id, purchases=purchases)


async def sync_pool_team_budgets(db: Session) -> dict:
    """
    Expire pool team budgets after the configured number of days.

    This cron job sets max_budget to $0 for pool teams whose last purchase
    was more than POOL_BUDGET_EXPIRATION_DAYS ago. Any remaining budget is
    lost after this period. Defaults to 365 days.

    Returns summary of updates made.
    """
    pool_teams = (
        db.query(DBTeam)
        .filter(
            DBTeam.budget_type == BudgetType.POOL,
            DBTeam.require_purchase_for_requests.is_(True),
        )
        .all()
    )

    total_updated = 0
    errors = []

    for team in pool_teams:
        latest_purchases_by_region = (
            db.query(DBPoolPurchase.region_id, func.max(DBPoolPurchase.purchased_at))
            .filter(DBPoolPurchase.team_id == team.id)
            .group_by(DBPoolPurchase.region_id)
            .all()
        )
        if not latest_purchases_by_region:
            continue

        # Check if ALL regions have expired
        all_regions_expired = True
        expired_region_ids = []

        for region_id, latest_purchase_at in latest_purchases_by_region:
            if not region_id or not latest_purchase_at:
                continue

            if latest_purchase_at.tzinfo is None:
                last_purchase = latest_purchase_at.replace(tzinfo=UTC)
            else:
                last_purchase = latest_purchase_at

            days_since_last_purchase = (datetime.now(UTC) - last_purchase).days
            if days_since_last_purchase < settings.POOL_BUDGET_EXPIRATION_DAYS:
                all_regions_expired = False
            else:
                expired_region_ids.append(region_id)

        if not expired_region_ids:
            continue

        if all_regions_expired:
            # All regions expired, propagate $0 budget to team and all keys
            team_had_any_update = False
            for rid in expired_region_ids:
                result = await propagate_team_budget_to_keys(
                    db,
                    team.id,
                    0.0,
                    f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d",
                    region_id=rid,
                    update_key_limits=False,
                    apply_to_keys=False,
                )
                errors.extend(result["errors"])
                if result["teams_updated"] > 0:
                    team_had_any_update = True
            if team_had_any_update:
                total_updated += 1
                logger.info(
                    f"Pool team {team.id} budget expired in all regions ({settings.POOL_BUDGET_EXPIRATION_DAYS}d passed), set to $0"
                )
        else:
            # Only some regions expired - update only those regions
            team_had_any_update = False
            for rid in expired_region_ids:
                result = await propagate_team_budget_to_keys(
                    db,
                    team.id,
                    0.0,
                    f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d",
                    region_id=rid,
                    update_key_limits=False,
                    apply_to_keys=False,
                )
                errors.extend(result["errors"])
                if result["teams_updated"] > 0:
                    team_had_any_update = True
                    logger.info(
                        f"Pool team {team.id} budget expired in region {rid} ({settings.POOL_BUDGET_EXPIRATION_DAYS}d passed), set to $0"
                    )
            if team_had_any_update:
                total_updated += 1

    return {"teams_updated": total_updated, "errors": errors}


async def sync_pool_team_monthly_caps(db: Session) -> dict:
    """
    Re-anchor POOL monthly caps at month boundaries.

    For POOL teams with monthly caps, LiteLLM team max_budget is set to:
    min(total_purchased_365d, month_start_spend + monthly_cap).
    """
    monthly_caps = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.budget_duration == MONTHLY_BUDGET_DURATION,
            DBSpendCap.max_budget.isnot(None),
            DBSpendCap.team_id.isnot(None),
            DBSpendCap.region_id.isnot(None),
        )
        .all()
    )
    current_anchor = _current_month_anchor()
    teams_updated = 0
    errors: list[str] = []

    for cap in monthly_caps:
        if cap.team_id is None or cap.region_id is None:
            continue
        team = db.query(DBTeam).filter(DBTeam.id == cap.team_id).first()
        if team is None or not team.requires_pool_purchase_gate:
            continue
        if cap.month_anchor == current_anchor:
            continue
        region = db.query(DBRegion).filter(DBRegion.id == cap.region_id).first()
        if region is None:
            continue
        try:
            service = LiteLLMService(
                api_url=region.litellm_api_url, api_key=region.litellm_api_key
            )
            lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
            team_info = (await service.get_team_info(lite_team_id)).get("team_info", {})
            month_start_spend = round(float(team_info.get("spend", 0.0) or 0.0), 4)
            total_purchased = (
                (
                    db.query(func.sum(DBPoolPurchase.amount_cents))
                    .filter(
                        DBPoolPurchase.team_id == team.id,
                        DBPoolPurchase.region_id == region.id,
                    )
                    .scalar()
                )
                or 0
            ) / 100.0
            effective_budget = _compute_pool_monthly_effective_budget(
                purchased_total=float(total_purchased),
                month_start_spend=month_start_spend,
                monthly_cap=float(cap.max_budget or 0.0),
            )
            await service.update_team_budget(
                team_id=lite_team_id,
                max_budget=effective_budget,
                budget_duration=_pool_budget_duration_from_last_purchase(
                    db=db, team_id=team.id, region_id=region.id
                ),
            )
            cap.month_anchor = current_anchor
            cap.month_start_spend = month_start_spend
            db.add(cap)
            db.commit()
            teams_updated += 1
        except Exception as exc:
            db.rollback()
            msg = (
                f"Failed monthly cap rollover for team_id={cap.team_id} "
                f"region_id={cap.region_id}: {str(exc)}"
            )
            logger.error(msg)
            errors.append(msg)

    return {"teams_updated": teams_updated, "errors": errors}
