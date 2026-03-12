from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import requests
import asyncpg
import logging
from datetime import datetime, UTC, timedelta

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import (
    Region,
    RegionCreate,
    RegionResponse,
    User,
    RegionUpdate,
    TeamSummary,
    TeamRegionBudget,
    BudgetPurchaseCreateRequest,
    BudgetPurchaseResponse,
)
from app.db.models import (
    DBRegion,
    DBPrivateAIKey,
    DBTeamRegion,
    DBTeam,
    DBUser,
    DBBudgetPurchase,
    DBLimitedResource,
)
from app.core.security import (
    get_role_min_system_admin,
    get_role_min_specific_team_admin,
)
from app.core.limit_service import LimitService, DEFAULT_MAX_SPEND
from app.schemas.limits import (
    ResourceType,
    OwnerType,
    LimitType,
    UnitType,
    LimitSource,
)
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["regions"])


def _pool_days_remaining(last_purchase_at: datetime | None) -> int:
    """
    Compute pool-budget days remaining from the last purchase timestamp.
    """
    if not last_purchase_at:
        return 0
    purchase_time = last_purchase_at
    if purchase_time.tzinfo is None:
        purchase_time = purchase_time.replace(tzinfo=UTC)
    return max(365 - (datetime.now(UTC) - purchase_time).days, 0)


def _pool_expires_at(last_purchase_at: datetime | None) -> datetime | None:
    """
    Compute pool-budget expiry timestamp from the last purchase timestamp.
    """
    if not last_purchase_at:
        return None
    purchase_time = last_purchase_at
    if purchase_time.tzinfo is None:
        purchase_time = purchase_time.replace(tzinfo=UTC)
    return purchase_time + timedelta(days=365)


async def validate_litellm_endpoint(api_url: str, api_key: str) -> bool:
    """
    Validate LiteLLM endpoint by making a test request to the health endpoint.

    Args:
        api_url: The LiteLLM API URL
        api_key: The LiteLLM API key

    Returns:
        bool: True if validation succeeds, raises HTTPException if it fails
    """
    try:
        # Test the LiteLLM health endpoint
        response = requests.get(
            f"{api_url}/health/liveliness",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        logger.info(f"LiteLLM endpoint validation successful for {api_url}")
        return True
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_details = e.response.json()
                error_msg = f"Status {e.response.status_code}: {error_details}"
            except ValueError:
                error_msg = f"Status {e.response.status_code}: {e.response.text}"
        logger.error(f"LiteLLM endpoint validation failed for {api_url}: {error_msg}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"LiteLLM endpoint validation failed: {error_msg}",
        )


async def validate_database_connection(
    host: str, port: int, user: str, password: str
) -> bool:
    """
    Validate database connection by attempting to connect to PostgreSQL.

    Args:
        host: Database host
        port: Database port
        user: Database admin user
        password: Database admin password

    Returns:
        bool: True if validation succeeds, raises HTTPException if it fails
    """
    try:
        # Attempt to connect to the database
        conn = await asyncpg.connect(host=host, port=port, user=user, password=password)
        await conn.close()
        logger.info(f"Database connection validation successful for {host}:{port}")
        return True
    except asyncpg.exceptions.PostgresError as e:
        logger.error(
            f"Database connection validation failed for {host}:{port}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database connection validation failed: {str(e)}",
        )
    except Exception as e:
        logger.error(
            f"Unexpected error during database validation for {host}:{port}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database connection validation failed: {str(e)}",
        )


@router.post(
    "", response_model=Region, dependencies=[Depends(get_role_min_system_admin)]
)
@router.post(
    "/", response_model=Region, dependencies=[Depends(get_role_min_system_admin)]
)
async def create_region(region: RegionCreate, db: Session = Depends(get_db)):
    # Check if region with this name already exists
    existing_region = db.query(DBRegion).filter(DBRegion.name == region.name).first()
    if existing_region:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A region with the name '{region.name}' already exists",
        )

    # Validate LiteLLM endpoint
    await validate_litellm_endpoint(region.litellm_api_url, region.litellm_api_key)

    # Validate database connection
    await validate_database_connection(
        region.postgres_host,
        region.postgres_port,
        region.postgres_admin_user,
        region.postgres_admin_password,
    )

    db_region = DBRegion(**region.model_dump())
    db.add(db_region)
    try:
        db.commit()
        db.refresh(db_region)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create region: {str(e)}",
        )
    return db_region


@router.get("", response_model=List[RegionResponse])
@router.get("/", response_model=List[RegionResponse])
async def list_regions(
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    # System admin users can see all regions
    if current_user.is_admin:
        return db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()

    # Regular users can only see non-dedicated regions
    if not current_user.team_id:
        return (
            db.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
            .all()
        )

    # Team members can see non-dedicated regions plus their team's dedicated regions
    team_dedicated_regions = (
        db.query(DBRegion)
        .join(DBTeamRegion)
        .filter(
            DBRegion.is_active.is_(True),
            DBRegion.is_dedicated.is_(True),
            DBTeamRegion.team_id == current_user.team_id,
        )
        .all()
    )

    non_dedicated_regions = (
        db.query(DBRegion)
        .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
        .all()
    )

    return non_dedicated_regions + team_dedicated_regions


@router.get(
    "/admin",
    response_model=List[Region],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def list_admin_regions(db: Session = Depends(get_db)):
    return db.query(DBRegion).all()


@router.get(
    "/{region_id}",
    response_model=RegionResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_region(region_id: int, db: Session = Depends(get_db)):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )
    return region


@router.delete("/{region_id}", dependencies=[Depends(get_role_min_system_admin)])
async def delete_region(region_id: int, db: Session = Depends(get_db)):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    # Check if there are any keys using this region
    existing_keys = (
        db.query(DBPrivateAIKey).filter(DBPrivateAIKey.region_id == region_id).count()
    )
    if existing_keys > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete region: {existing_keys} keys(s) are currently using this region. Please delete these keys first.",
        )

    # Instead of deleting, mark as inactive
    region.is_active = False
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete region: {str(e)}",
        )
    return {"message": "Region deleted successfully"}


@router.put(
    "/{region_id}",
    response_model=Region,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def update_region(
    region_id: int, region: RegionUpdate, db: Session = Depends(get_db)
):
    db_region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not db_region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    # Check if updating to a name that already exists (excluding current region)
    if region.name != db_region.name:
        existing_region = (
            db.query(DBRegion)
            .filter(DBRegion.name == region.name, DBRegion.id != region_id)
            .first()
        )
        if existing_region:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A region with the name '{region.name}' already exists",
            )

    # Update the region fields
    update_data = region.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_region, field, value)

    try:
        db.commit()
        db.refresh(db_region)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update region: {str(e)}",
        )
    return db_region


@router.post(
    "/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)]
)
async def associate_team_with_region(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    """Associate a team with a dedicated region. Only system admins can do this."""

    # Check if region exists and is dedicated
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only associate teams with dedicated regions",
        )

    # Check if team exists
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    # Check if association already exists
    existing_association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id)
        .first()
    )

    if existing_association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is already associated with this region",
        )

    # Create the association
    team_region = DBTeamRegion(team_id=team_id, region_id=region_id)
    db.add(team_region)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to associate team with region: {str(e)}",
        )

    return {"message": "Team associated with region successfully"}


@router.delete(
    "/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)]
)
async def disassociate_team_from_region(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    """Disassociate a team from a dedicated region. Only system admins can do this."""

    # Check if association exists
    association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id)
        .first()
    )

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team-region association not found",
        )

    # Remove the association
    db.delete(association)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disassociate team from region: {str(e)}",
        )

    return {"message": "Team disassociated from region successfully"}


@router.get(
    "/{region_id}/teams",
    response_model=List[TeamSummary],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def list_teams_for_region(region_id: int, db: Session = Depends(get_db)):
    """List teams associated with a dedicated region. Only system admins can do this."""

    # Check if region exists and is dedicated
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only list teams for dedicated regions",
        )

    # Get associated teams
    teams = (
        db.query(DBTeam)
        .join(DBTeamRegion)
        .filter(DBTeamRegion.region_id == region_id)
        .all()
    )

    return teams


@router.get(
    "/{region_id}/teams/{team_id}/budget",
    response_model=TeamRegionBudget,
    dependencies=[Depends(get_role_min_specific_team_admin)],
)
async def get_team_region_budget(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    """
    Get total budget and spend for a team in a specific region.
    """
    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    region = (
        db.query(DBRegion)
        .filter(DBRegion.id == region_id, DBRegion.is_active.is_(True))
        .first()
    )
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    if team.budget_mode == "pool":
        team_region = (
            db.query(DBTeamRegion)
            .filter(
                DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id
            )
            .first()
        )
        if not team_region:
            raise HTTPException(
                status_code=404, detail="Team-region association not found"
            )

        aggregate_spend_cents = int(team_region.aggregate_spend_cents or 0)
        total_budget_cents = int(team_region.total_budget_purchased_cents or 0)
        available_budget_cents = max(total_budget_cents - aggregate_spend_cents, 0)

        days_remaining = 0
        expires_at = None
        if team_region.last_budget_purchase_at:
            days_remaining = _pool_days_remaining(team_region.last_budget_purchase_at)
            expires_at = _pool_expires_at(team_region.last_budget_purchase_at)

        return TeamRegionBudget(
            team_id=team_id,
            region_id=region_id,
            region_name=region.name,
            total_spend=round(aggregate_spend_cents / 100.0, 4),
            total_budget=round(total_budget_cents / 100.0, 4),
            days_remaining=days_remaining,
            expires_at=expires_at,
            aggregate_spend_cents=aggregate_spend_cents,
            available_budget_cents=available_budget_cents,
        )

    team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
    team_user_ids = [user.id for user in team_users]

    team_keys = (
        db.query(DBPrivateAIKey)
        .filter(
            DBPrivateAIKey.region_id == region_id,
            (DBPrivateAIKey.team_id == team_id)
            | (DBPrivateAIKey.owner_id.in_(team_user_ids)),
        )
        .all()
    )

    limit_service = LimitService(db)
    total_spend = 0.0
    total_budget = 0.0

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    for key in team_keys:
        if not key.litellm_token:
            continue

        key_spend = 0.0
        max_budget = None
        try:
            key_info = await litellm_service.get_key_info(key.litellm_token)
            info = key_info.get("info", {})
            key_spend = float(info.get("spend", 0.0) or 0.0)
            max_budget = info.get("max_budget")
        except Exception as exc:
            logger.warning(
                "Failed to get LiteLLM info for key %s in region %s: %s",
                key.id,
                region.name,
                str(exc),
            )
            if key.cached_spend is not None:
                key_spend = float(key.cached_spend)

        total_spend += key_spend

        if max_budget is None:
            try:
                if key.owner_id:
                    owner = db.query(DBUser).filter(DBUser.id == key.owner_id).first()
                    limits = limit_service.get_user_limits(owner) if owner else []
                else:
                    limits = limit_service.get_team_limits(team)

                budget_limit = next(
                    (
                        limit
                        for limit in limits
                        if limit.resource == ResourceType.BUDGET
                    ),
                    None,
                )
                max_budget = budget_limit.max_value if budget_limit else None
            except Exception:
                max_budget = None

            if max_budget is None:
                try:
                    max_budget = limit_service.get_default_team_limit_for_resource(
                        ResourceType.BUDGET
                    )
                except Exception:
                    max_budget = DEFAULT_MAX_SPEND

        total_budget += float(max_budget or 0.0)

    return TeamRegionBudget(
        team_id=team_id,
        region_id=region_id,
        region_name=region.name,
        total_spend=round(total_spend, 4),
        total_budget=round(total_budget, 4),
    )


@router.post(
    "/{region_id}/teams/{team_id}/budget-purchases",
    response_model=BudgetPurchaseResponse,
    dependencies=[Depends(get_role_min_specific_team_admin)],
)
async def create_team_budget_purchase(
    region_id: int,
    team_id: int,
    request_data: BudgetPurchaseCreateRequest,
    db: Session = Depends(get_db),
):
    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if team.budget_mode != "pool":
        raise HTTPException(
            status_code=400,
            detail="Pool budget purchases require team budget_mode='pool'",
        )

    region = (
        db.query(DBRegion)
        .filter(DBRegion.id == region_id, DBRegion.is_active.is_(True))
        .first()
    )
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id)
        .with_for_update()
        .first()
    )
    if not association:
        raise HTTPException(status_code=404, detail="Team-region association not found")

    external_purchase_id = request_data.external_purchase_id.strip()
    if not external_purchase_id:
        raise HTTPException(
            status_code=400, detail="external_purchase_id must not be blank"
        )
    existing_purchase = (
        db.query(DBBudgetPurchase)
        .filter(DBBudgetPurchase.stripe_session_id == external_purchase_id)
        .first()
    )
    currency = request_data.currency.lower()
    purchase_time = request_data.purchased_at or datetime.now(UTC)
    if purchase_time.tzinfo is None:
        purchase_time = purchase_time.replace(tzinfo=UTC)

    if existing_purchase:
        if (
            existing_purchase.team_id != team_id
            or existing_purchase.region_id != region_id
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Purchase with this external_purchase_id already exists for "
                    "a different team or region"
                ),
            )

        if (
            existing_purchase.amount_cents != request_data.amount_cents
            or existing_purchase.currency.lower() != currency
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Purchase with this external_purchase_id already exists with "
                    "different amount or currency"
                ),
            )

        aggregate_spend_cents = int(association.aggregate_spend_cents or 0)
        available_budget_cents = max(
            int(association.total_budget_purchased_cents or 0) - aggregate_spend_cents,
            0,
        )
        days_remaining = _pool_days_remaining(association.last_budget_purchase_at)
        expires_at = _pool_expires_at(association.last_budget_purchase_at)
        if not expires_at:
            expires_at = purchase_time + timedelta(days=365)
            days_remaining = 365

        return BudgetPurchaseResponse(
            external_purchase_id=external_purchase_id,
            previous_budget_cents=existing_purchase.previous_budget_cents,
            amount_added_cents=existing_purchase.amount_cents,
            new_budget_cents=existing_purchase.new_budget_cents,
            aggregate_spend_cents=aggregate_spend_cents,
            available_budget_cents=available_budget_cents,
            expires_at=expires_at,
            days_remaining=days_remaining,
        )

    budget_limit = (
        db.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == team_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .with_for_update()
        .first()
    )
    previous_budget_cents = int(
        round((budget_limit.max_value if budget_limit else 0.0) * 100)
    )
    amount_cents = int(request_data.amount_cents)
    new_budget_cents = previous_budget_cents + amount_cents

    if budget_limit:
        budget_limit.max_value = new_budget_cents / 100.0
        budget_limit.limited_by = LimitSource.MANUAL
        budget_limit.set_by = "external_pool_budget_purchase"
        budget_limit.updated_at = datetime.now(UTC)
    else:
        budget_limit = DBLimitedResource(
            limit_type=LimitType.DATA_PLANE,
            resource=ResourceType.BUDGET,
            unit=UnitType.DOLLAR,
            max_value=new_budget_cents / 100.0,
            current_value=None,
            owner_type=OwnerType.TEAM,
            owner_id=team_id,
            limited_by=LimitSource.MANUAL,
            set_by="external_pool_budget_purchase",
        )
        db.add(budget_limit)

    association.total_budget_purchased_cents = int(
        (association.total_budget_purchased_cents or 0) + amount_cents
    )
    association.last_budget_purchase_at = purchase_time
    association.expiry_notification_sent_at = None
    association.updated_at = datetime.now(UTC)
    db.add(association)

    purchase = DBBudgetPurchase(
        team_id=team_id,
        region_id=region_id,
        stripe_session_id=external_purchase_id,
        stripe_payment_intent_id=request_data.stripe_payment_intent_id,
        currency=currency,
        amount_cents=amount_cents,
        previous_budget_cents=previous_budget_cents,
        new_budget_cents=new_budget_cents,
        purchased_at=purchase_time,
    )
    db.add(purchase)
    db.commit()

    try:
        limit_service = LimitService(db)
        limit_service._trigger_team_budget_propagation(
            team_id=team_id,
            budget_amount=(new_budget_cents / 100.0),
            region_id=region_id,
        )
    except Exception as propagation_exc:
        logger.error(
            "Failed to propagate externally purchased pool budget for team=%s region=%s: %s",
            team_id,
            region_id,
            str(propagation_exc),
        )

    aggregate_spend_cents = int(association.aggregate_spend_cents or 0)
    available_budget_cents = max(
        int(association.total_budget_purchased_cents or 0) - aggregate_spend_cents, 0
    )
    days_remaining = _pool_days_remaining(association.last_budget_purchase_at)
    expires_at = _pool_expires_at(association.last_budget_purchase_at)
    if not expires_at:
        expires_at = purchase_time + timedelta(days=365)
        days_remaining = 365

    return BudgetPurchaseResponse(
        external_purchase_id=external_purchase_id,
        previous_budget_cents=previous_budget_cents,
        amount_added_cents=amount_cents,
        new_budget_cents=new_budget_cents,
        aggregate_spend_cents=aggregate_spend_cents,
        available_budget_cents=available_budget_cents,
        expires_at=expires_at,
        days_remaining=days_remaining,
    )
