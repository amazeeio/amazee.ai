from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, UTC
import logging

from app.db.database import get_db
from app.db.models import DBTeam, DBTeamProduct, DBUser, DBPrivateAIKey, DBRegion, DBTeamRegion, DBProduct
from app.core.security import get_role_min_system_admin, get_role_min_specific_team_admin, get_current_user_from_auth, check_sales_or_higher
from app.schemas.models import (
    Team, TeamCreate, TeamUpdate,
    TeamWithUsers, TeamMergeRequest, TeamMergeResponse
)
from app.core.resource_limits import DEFAULT_KEY_DURATION, DEFAULT_MAX_SPEND, DEFAULT_RPM_PER_KEY
from app.services.litellm import LiteLLMService
from app.services.ses import SESService
from app.core.worker import get_team_keys_by_region, generate_pricing_url, get_team_admin_email
from app.api.private_ai_keys import delete_private_ai_key
from app.schemas.models import SalesTeamsResponse, SalesProduct, SalesTeam


logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["teams"]
)

@router.post("", response_model=Team, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=Team, status_code=status.HTTP_201_CREATED)
async def register_team(
    team: TeamCreate,
    db: Session = Depends(get_db)
):
    """
    Register a new team. This endpoint is publicly accessible.
    """
    # Check if team email already exists (case insensitive)
    db_team = db.query(DBTeam).filter(func.lower(DBTeam.admin_email) == func.lower(team.admin_email)).first()
    if db_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Check if team name already exists (case insensitive)
    db_team = db.query(DBTeam).filter(func.lower(DBTeam.name) == func.lower(team.name)).first()
    if db_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team name already exists"
        )

    # Create the team
    db_team = DBTeam(
        name=team.name,
        admin_email=team.admin_email,
        phone=team.phone,
        billing_address=team.billing_address,
        is_active=True,
        created_at=datetime.now(UTC)
    )

    db.add(db_team)
    db.commit()
    db.refresh(db_team)

    return db_team

@router.get("", response_model=List[Team], dependencies=[Depends(get_role_min_system_admin)])
@router.get("/", response_model=List[Team], dependencies=[Depends(get_role_min_system_admin)])
async def list_teams(
    db: Session = Depends(get_db)
):
    """
    List all teams. Only accessible by admin users.
    """
    return db.query(DBTeam).all()

@router.get("/{team_id}", response_model=TeamWithUsers, dependencies=[Depends(get_role_min_specific_team_admin)])
async def get_team(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a team by ID. Accessible by admin users or users associated with the team.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Convert directly to TeamWithUsers model
    return TeamWithUsers.model_validate(db_team)

@router.put("/{team_id}", response_model=Team, dependencies=[Depends(get_role_min_specific_team_admin)])
async def update_team(
    team_id: int,
    team_update: TeamUpdate,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user_from_auth)
):
    """
    Update a team. Accessible by admin users or team admins.
    Only system admins can toggle the always-free status.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check if trying to update is_always_free without system admin privileges
    if team_update.is_always_free is not None and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only system administrators can toggle always-free status"
        )

    # Update team fields
    for key, value in team_update.model_dump(exclude_unset=True).items():
        setattr(db_team, key, value)

    db_team.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_team)

    # Only send email when turning always-free on
    if team_update.is_always_free:
        try:
            admin_email = get_team_admin_email(db, db_team)
            ses_service = SESService()
            template_data = {
                "name": db_team.name,
                "dashboard_url": generate_pricing_url(admin_email)
            }
            ses_service.send_email(
                to_addresses=[admin_email],
                template_name="always-free",
                template_data=template_data
            )
        except Exception as e:
            logger.error(f"Failed to send always-free status update email to team {db_team.name}: {str(e)}")
            # Don't fail the request if email fails

    return db_team

@router.delete("/{team_id}", dependencies=[Depends(get_role_min_system_admin)])
async def delete_team(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a team. Only accessible by admin users.
    First removes all product associations, then deletes the team.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Remove all product associations
    db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team_id).delete()

    # Delete the team
    db.delete(db_team)
    db.commit()

    return {"message": "Team deleted successfully"}

@router.post("/{team_id}/extend-trial", dependencies=[Depends(get_role_min_system_admin)])
async def extend_team_trial(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Extend a team's trial period. This will:
    1. Update the team's last payment date to now
    2. Reset all resource limits to default values
    3. Send a trial extension email notification

    Only accessible by system admin users.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Update the last payment date to now
    db_team.last_payment = datetime.now(UTC)
    db.commit()

    # Get all keys for the team grouped by region
    keys_by_region = get_team_keys_by_region(db, team_id)

    # Update keys for each region
    for region, keys in keys_by_region.items():
        # Initialize LiteLLM service for this region
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key
        )

        # Update each key's duration and budget via LiteLLM
        for key in keys:
            try:
                await litellm_service.set_key_restrictions(
                    litellm_token=key.litellm_token,
                    duration=f"{DEFAULT_KEY_DURATION}d",
                    budget_duration=f"{DEFAULT_KEY_DURATION}d",
                    budget_amount=DEFAULT_MAX_SPEND,
                    rpm_limit=DEFAULT_RPM_PER_KEY
                )
            except Exception as e:
                logger.error(f"Failed to update key {key.id} via LiteLLM: {str(e)}")
                # Continue with other keys even if one fails

    # Send trial extension email
    try:
        ses_service = SESService()
        template_data = {
            "name": db_team.name,
        }
        ses_service.send_email(
            to_addresses=[db_team.admin_email],
            template_name="trial-extended",
            template_data=template_data
        )
    except Exception as e:
        logger.error(f"Failed to send trial extension email to team {db_team.name}: {str(e)}")
        # Don't fail the request if email fails

    return {"message": "Team trial extended successfully"}

@router.get("/sales/list-teams", response_model=SalesTeamsResponse, dependencies=[Depends(check_sales_or_higher)])
async def list_teams_for_sales(
    db: Session = Depends(get_db)
):
    """
    Get consolidated team information for sales dashboard.
    Returns all teams with their products, regions, spend data, and trial status.
    Accessible by system admin and sales users.
    """
    try:
        # Track unreachable endpoints for logging at the end (use set to avoid duplicates)
        unreachable_endpoints = set()

        # Pre-fetch all regions once to avoid repeated queries
        all_regions = db.query(DBRegion).filter(DBRegion.is_active == True).all()
        regions_map = {r.id: r for r in all_regions}

        # Pre-create LiteLLM services for each region to avoid re-instantiation
        litellm_services = {}
        for region in all_regions:
            litellm_services[region.id] = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )

        # Get all teams with their basic information
        teams = db.query(DBTeam).all()

        sales_teams = []

        for team in teams:
            # Get team products
            team_products = db.query(DBTeamProduct).join(DBProduct).filter(
                DBTeamProduct.team_id == team.id,
                DBProduct.active == True
            ).all()

            products = [
                SalesProduct(
                    id=team_product.product.id,
                    name=team_product.product.name,
                    active=team_product.product.active
                )
                for team_product in team_products
            ]

            # Get team AI keys (both team-owned and user-owned) and calculate total spend
            team_users = db.query(DBUser).filter(DBUser.team_id == team.id).all()
            team_user_ids = [user.id for user in team_users]

            team_keys = db.query(DBPrivateAIKey).filter(
                (DBPrivateAIKey.team_id == team.id) |  # Team-owned keys
                (DBPrivateAIKey.owner_id.in_(team_user_ids))  # User-owned keys by team members
            ).all()

            # Calculate total spend from all AI keys and build regions list as we go
            total_spend = 0.0
            regions_set = set()

            for key in team_keys:
                if key.litellm_token and key.region_id in regions_map:
                    try:
                        # Use pre-fetched region info and pre-created LiteLLM service
                        region = regions_map[key.region_id]
                        litellm_service = litellm_services[region.id]

                        # Add region name to our set
                        regions_set.add(region.name)

                        # Get spend data from LiteLLM
                        key_data = await litellm_service.get_key_info(key.litellm_token)
                        key_spend = key_data.get("info", {}).get("spend", 0.0)
                        total_spend += float(key_spend)
                    except Exception as e:
                        # Track unreachable endpoint for logging at the end (only once per region)
                        region = regions_map[key.region_id]
                        endpoint_info = f"Region: {region.name}"
                        unreachable_endpoints.add(endpoint_info)

            # Convert set to list for the response
            regions = list(regions_set)

            # Calculate trial status
            trial_status = _calculate_trial_status(team, products)

            sales_team = SalesTeam(
                id=team.id,
                name=team.name,
                admin_email=team.admin_email,
                created_at=team.created_at,
                last_payment=team.last_payment,
                is_always_free=team.is_always_free,
                products=products,
                regions=regions,
                total_spend=round(total_spend, 4),
                trial_status=trial_status
            )

            sales_teams.append(sales_team)

        # Log all unreachable endpoints at the end
        if unreachable_endpoints:
            logger.warning(f"Unreachable LiteLLM endpoints encountered: {len(unreachable_endpoints)} unique endpoints")
            for endpoint in unreachable_endpoints:
                logger.warning(f"  - {endpoint}")

        return SalesTeamsResponse(teams=sales_teams)

    except Exception as e:
        logger.error(f"Error in list_teams_for_sales: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sales data: {str(e)}"
        )

def _calculate_trial_status(team: DBTeam, products: List[SalesProduct]) -> str:
    """
    Calculate trial status based on team creation, last payment, and active products.
    """
    if team.is_always_free:
        return "Always Free"

    if len(products) > 0:
        return "Active Product"

    # Calculate days until expiry
    trial_period_days = 30
    if team.last_payment:
        days_since_last_payment = (datetime.now(UTC) - team.last_payment.replace(tzinfo=UTC)).days
        days_remaining = trial_period_days - days_since_last_payment
    else:
        days_since_creation = (datetime.now(UTC) - team.created_at.replace(tzinfo=UTC)).days
        days_remaining = trial_period_days - days_since_creation

    if days_remaining <= 0:
        return "Expired"
    else:
        # Always show days remaining for active trials
        return f"{days_remaining} days left"

def _check_key_name_conflicts(team1_keys: List[DBPrivateAIKey], team2_keys: List[DBPrivateAIKey]) -> List[str]:
    """Return list of conflicting key names between two teams"""
    team1_names = {key.name for key in team1_keys if key.name}
    team2_names = {key.name for key in team2_keys if key.name}
    return list(team1_names.intersection(team2_names))

async def _resolve_key_conflicts(
    conflicts: List[str],
    strategy: str,
    team2_keys: List[DBPrivateAIKey],
    rename_suffix: str,
    db: Session = None,
    current_user = None
) -> List[DBPrivateAIKey]:
    """Apply conflict resolution strategy to team2 keys"""
    if strategy == "delete":
        # Remove conflicting keys from team2 and delete them from database
        keys_to_delete = [key for key in team2_keys if key.name in conflicts]
        remaining_keys = [key for key in team2_keys if key.name not in conflicts]

        # Delete conflicting keys from database if db session provided
        if db and current_user:
            for key in keys_to_delete:
                try:
                    await delete_private_ai_key(
                        key_id=key.id,
                        current_user=current_user,
                        user_role="system_admin",  # System admin context for merge operations
                        db=db
                    )
                except Exception as e:
                    logger.error(f"Failed to delete key {key.id}: {str(e)}")
                    # Continue with other keys even if one fails

        return remaining_keys
    elif strategy == "rename":
        # Rename conflicting keys in team2
        suffix = rename_suffix
        for key in team2_keys:
            if key.name in conflicts:
                key.name = f"{key.name}{suffix}"
        return team2_keys
    elif strategy == "cancel":
        # Return original keys unchanged
        return team2_keys
    else:
        raise ValueError(f"Unknown conflict resolution strategy: {strategy}")

@router.post("/{target_team_id}/merge", dependencies=[Depends(get_role_min_system_admin)])
async def merge_teams(
    target_team_id: int,
    merge_request: TeamMergeRequest,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user_from_auth)
):
    """
    Merge source team into target team. Only accessible by system administrators.

    This endpoint will:
    1. Validate both teams exist
    2. Check if source team has active product associations (fails if it does)
    3. Check for key name conflicts
    4. Apply conflict resolution strategy
    5. Migrate users and keys
    6. Update LiteLLM key associations
    7. Delete the source team
    """
    try:
        # Validate teams exist
        target_team = db.query(DBTeam).filter(DBTeam.id == target_team_id).first()
        if not target_team:
            raise HTTPException(status_code=404, detail="Target team not found")

        source_team = db.query(DBTeam).filter(DBTeam.id == merge_request.source_team_id).first()
        if not source_team:
            raise HTTPException(status_code=404, detail="Source team not found")

        # Prevent merging a team into itself
        if source_team.id == target_team.id:
            raise HTTPException(
                status_code=400,
                detail="Cannot merge a team into itself"
            )

        # Check if source team has active product associations first
        source_products = db.query(DBTeamProduct).filter(DBTeamProduct.team_id == source_team.id).all()
        if source_products:
            product_names = [product.product_id for product in source_products]
            raise HTTPException(
                status_code=400,
                detail=f"Cannot merge team '{source_team.name}' - it has active product associations: {', '.join(product_names)}. Please remove product associations before merging."
            )

        # Check if source team has dedicated region associations
        source_dedicated_regions = db.query(DBTeamRegion).filter(DBTeamRegion.team_id == source_team.id).all()
        if source_dedicated_regions:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot merge team '{source_team.name}' - it has dedicated region associations. Please remove the association before merging."
            )

        # Get team keys and users (only if no product associations found)
        source_keys = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.team_id == source_team.id).all()
        target_keys = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.team_id == target_team.id).all()
        source_users = db.query(DBUser).filter(DBUser.team_id == source_team.id).all()

        # Check for conflicts
        conflicts = _check_key_name_conflicts(target_keys, source_keys)

        # Apply conflict resolution strategy
        if conflicts:
            if merge_request.conflict_resolution_strategy == "cancel":
                return TeamMergeResponse(
                    success=False,
                    message=f"Merge cancelled due to {len(conflicts)} key name conflicts",
                    conflicts_resolved=conflicts,
                    keys_migrated=0,
                    users_migrated=0
                )

            source_keys = await _resolve_key_conflicts(
                conflicts,
                merge_request.conflict_resolution_strategy,
                source_keys,
                merge_request.rename_suffix if merge_request.rename_suffix is not None else f"_team{source_team.id}",
                db,
                current_user
            )

        # Store team names before deletion
        source_team_name = source_team.name
        target_team_name = target_team.name

        # Migrate users from source team to target team
        users_migrated = 0
        for user in source_users:
            if user.team_id != target_team.id:
                user.team_id = target_team.id
                users_migrated += 1

        # Migrate keys from source team to target team
        keys_migrated = 0
        for key in source_keys:
            if key.team_id != target_team.id:
                key.team_id = target_team.id
                keys_migrated += 1

        # Flush changes to ensure they're persisted in the current transaction
        db.flush()

        # Update LiteLLM key associations
        # Create a map of keys by region to avoid unnecessary DB queries
        keys_by_region = {}
        for key in source_keys:
            if key.region_id not in keys_by_region:
                keys_by_region[key.region_id] = []
            keys_by_region[key.region_id].append(key)

        # Update LiteLLM key associations for each region
        for region_id, region_keys in keys_by_region.items():
            # Get region info
            region = db.query(DBRegion).filter(
                DBRegion.id == region_id,
                DBRegion.is_active == True
            ).first()

            # Initialize LiteLLM service for this region
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )

            # Update team association for each key in this region
            for key in region_keys:
                try:
                    await litellm_service.update_key_team_association(
                        key.litellm_token,
                        LiteLLMService.format_team_id(region.name, target_team.id)
                    )
                except Exception as e:
                    logger.error(f"Failed to update LiteLLM key {key.id}: {str(e)}")

        # Delete source team
        db.delete(source_team)
        db.commit()

        return TeamMergeResponse(
            success=True,
            message=f"Successfully merged team '{source_team_name}' into '{target_team_name}'",
            conflicts_resolved=conflicts if conflicts else None,
            keys_migrated=keys_migrated,
            users_migrated=users_migrated
        )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error during team merge: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Team merge failed: {str(e)}"
        )
