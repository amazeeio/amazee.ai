from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from datetime import datetime, UTC
import logging

from app.db.database import get_db
from app.db.models import DBTeam, DBRegion, DBPoolPurchase
from app.core.security import get_role_min_system_admin
from app.core.config import settings
from app.schemas.models import (
    PoolPurchaseRequest,
    PoolPurchaseResponse,
    PoolPurchaseHistoryResponse,
    PoolRegionPurchaseHistoryResponse,
    BudgetType,
)
from app.services.litellm import LiteLLMService


logger = logging.getLogger(__name__)

router = APIRouter(tags=["budgets"])


@router.post(
    "/region/{region_id}/teams/{team_id}/purchase",
    response_model=PoolPurchaseResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def purchase_pool_budget(
    region_id: int,
    team_id: int,
    purchase: PoolPurchaseRequest,
    db: Session = Depends(get_db),
):
    """
    Record a pool budget purchase for a team and update their LiteLLM team budget.

    Only works for teams with budget_type = POOL.
    Handles concurrent purchases by checking for duplicate stripe_payment_id.
    """
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    if team.budget_type != BudgetType.POOL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This endpoint only works for teams with pool budget type",
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

    amount_dollars = purchase.amount_cents / 100.0

    # Flush before external side effects so stripe_payment_id uniqueness is
    # enforced in this transaction (handles concurrent duplicate requests).
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)

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

    try:
        await litellm_service.update_team_budget(
            team_id=lite_team_id,
            max_budget=new_total_budget,
            budget_duration=f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d",
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update LiteLLM team budget: {e}")
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
        f"${amount_dollars:.2f} added, new total budget: ${new_total_budget:.2f}"
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
    pool_teams = db.query(DBTeam).filter(DBTeam.budget_type == BudgetType.POOL).all()

    total_updated = 0
    errors = []

    regions_cache = {}

    for team in pool_teams:
        latest_purchases_by_region = (
            db.query(DBPoolPurchase.region_id, func.max(DBPoolPurchase.purchased_at))
            .filter(DBPoolPurchase.team_id == team.id)
            .group_by(DBPoolPurchase.region_id)
            .all()
        )
        if not latest_purchases_by_region:
            continue

        team_had_successful_expiration = False

        for region_id, latest_purchase_at in latest_purchases_by_region:
            if not region_id or not latest_purchase_at:
                continue

            if latest_purchase_at.tzinfo is None:
                last_purchase = latest_purchase_at.replace(tzinfo=UTC)
            else:
                last_purchase = latest_purchase_at

            days_since_last_purchase = (datetime.now(UTC) - last_purchase).days
            if days_since_last_purchase < settings.POOL_BUDGET_EXPIRATION_DAYS:
                continue

            if region_id not in regions_cache:
                regions_cache[region_id] = (
                    db.query(DBRegion).filter(DBRegion.id == region_id).first()
                )
            region = regions_cache[region_id]
            if not region:
                continue

            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url, api_key=region.litellm_api_key
            )
            lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

            try:
                await litellm_service.update_team_budget(
                    team_id=lite_team_id,
                    max_budget=0.0,
                )
                team_had_successful_expiration = True
                logger.info(
                    f"Pool team {team.id} budget expired in region {region.id} ({settings.POOL_BUDGET_EXPIRATION_DAYS}d passed), set to $0"
                )
            except Exception as e:
                errors.append(f"Team {team.id}, region {region_id}: {str(e)}")
                logger.error(
                    f"Failed to expire team {team.id} budget in region {region_id}: {e}"
                )

        if team_had_successful_expiration:
            total_updated += 1

    return {"teams_updated": total_updated, "errors": errors}
