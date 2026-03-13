from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from datetime import datetime, UTC
import logging
from collections import defaultdict

from app.db.database import get_db
from app.db.models import DBTeam, DBRegion, DBPoolPurchase
from app.core.security import get_role_min_system_admin
from app.schemas.models import PoolPurchaseRequest, PoolPurchaseResponse
from app.services.litellm import LiteLLMService


logger = logging.getLogger(__name__)

router = APIRouter(tags=["budgets"])


@router.post(
    "/region/{region_id}/teams/{team_id}/purchase",
    response_model=PoolPurchaseResponse,
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

    if team.budget_type != "pool":
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

    try:
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
        db.commit()
        db.refresh(purchase_record)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A purchase with this stripe_payment_id already exists",
        )

    team.last_pool_purchase = purchase.purchased_at
    db.commit()

    amount_dollars = purchase.amount_cents / 100.0

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)

    total_purchased_cents = (
        db.query(func.sum(DBPoolPurchase.amount_cents))
        .filter(DBPoolPurchase.team_id == team_id)
        .scalar()
        or 0
    )
    total_purchased_dollars = total_purchased_cents / 100.0

    try:
        team_info = await litellm_service.get_team_info(lite_team_id)
        current_spend = team_info.get("spend") or 0.0
    except Exception as e:
        logger.warning(f"Could not get team info from LiteLLM, assuming $0 spend: {e}")
        current_spend = 0.0

    remaining_budget = total_purchased_dollars - current_spend
    if remaining_budget < 0:
        remaining_budget = 0.0

    new_total_budget = remaining_budget + amount_dollars

    try:
        await litellm_service.update_team_budget(
            team_id=lite_team_id, max_budget=new_total_budget, budget_duration="365d"
        )
    except Exception as e:
        logger.error(f"Failed to update LiteLLM team budget: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update team budget in LiteLLM: {str(e)}",
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


async def sync_pool_team_budgets(db: Session) -> dict:
    """
    Sync pool team budgets with LiteLLM.

    This cron job ensures that:
    - Pool teams have correct max_budget in LiteLLM based on purchases - spend
    - After 365d (budget_duration), any remaining budget is lost

    Returns summary of updates made.
    """
    pool_teams = (
        db.query(DBTeam)
        .filter(DBTeam.budget_type == "pool", DBTeam.last_pool_purchase.isnot(None))
        .all()
    )

    teams_by_region = defaultdict(list)
    for team in pool_teams:
        latest_purchase = (
            db.query(DBPoolPurchase)
            .filter(DBPoolPurchase.team_id == team.id)
            .order_by(DBPoolPurchase.purchased_at.desc())
            .first()
        )
        if not latest_purchase:
            continue

        region = latest_purchase.region
        if region not in teams_by_region:
            teams_by_region[region] = []
        if team not in teams_by_region[region]:
            teams_by_region[region].append(team)

    total_updated = 0
    errors = []

    for region, teams in teams_by_region.items():
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )

        for team in teams:
            try:
                latest_purchase = (
                    db.query(DBPoolPurchase)
                    .filter(DBPoolPurchase.team_id == team.id)
                    .filter(DBPoolPurchase.region_id == region.id)
                    .order_by(DBPoolPurchase.purchased_at.desc())
                    .first()
                )

                days_since_last_purchase = (
                    datetime.now(UTC) - latest_purchase.purchased_at.replace(tzinfo=UTC)
                ).days

                total_purchased_cents = (
                    db.query(func.sum(DBPoolPurchase.amount_cents))
                    .filter(DBPoolPurchase.team_id == team.id)
                    .filter(DBPoolPurchase.region_id == region.id)
                    .scalar()
                    or 0
                )
                total_purchased_dollars = total_purchased_cents / 100.0

                lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

                if days_since_last_purchase >= 365:
                    new_budget = 0.0
                    logger.info(
                        f"Pool team {team.id} budget expired (365d passed), setting to $0"
                    )
                else:
                    try:
                        team_info = await litellm_service.get_team_info(lite_team_id)
                        current_spend = team_info.get("spend") or 0.0
                    except Exception as e:
                        logger.warning(
                            f"Could not get team info for team {team.id}: {e}"
                        )
                        continue

                    remaining_budget = total_purchased_dollars - current_spend
                    if remaining_budget < 0:
                        remaining_budget = 0.0
                    new_budget = remaining_budget

                try:
                    await litellm_service.update_team_budget(
                        team_id=lite_team_id,
                        max_budget=new_budget,
                        budget_duration="365d",
                    )
                    total_updated += 1
                    logger.info(f"Synced pool team {team.id} budget: ${new_budget:.2f}")
                except Exception as e:
                    errors.append(f"Team {team.id}: {str(e)}")
                    logger.error(f"Failed to update team {team.id} budget: {e}")

            except Exception as e:
                errors.append(f"Team {team.id}: {str(e)}")
                logger.error(f"Error syncing team {team.id}: {e}")

    return {"teams_updated": total_updated, "errors": errors}
