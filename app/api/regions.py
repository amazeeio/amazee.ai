from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import requests
import asyncpg
import logging

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.core.roles import UserRole
from app.schemas.models import (
    Region,
    RegionCreate,
    RegionResponse,
    User,
    RegionUpdate,
    TeamSummary,
    TeamRegionBudget,
    TeamRegionModelAliasesResponse,
    TeamRegionModelAliasesUpdateRequest,
)
from app.db.models import (
    DBRegion,
    DBPrivateAIKey,
    DBTeamRegion,
    DBTeam,
    DBUser,
    DBUserAdminRegion,
)
from app.core.security import (
    get_role_min_system_admin,
    get_role_min_specific_team_admin,
)
from app.core.config import settings
from app.core.limit_service import LimitService, DEFAULT_MAX_SPEND
from app.core.litellm_user_sync import (
    sync_add_user_to_team,
    sync_remove_user_from_team,
)
from app.schemas.limits import ResourceType
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["regions"])


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

    # Team members can only see their team's explicitly assigned regions.
    return (
        db.query(DBRegion)
        .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
        .filter(
            DBRegion.is_active.is_(True),
            DBTeamRegion.team_id == current_user.team_id,
        )
        .all()
    )


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


def _assert_team_region_write_access(
    db: Session,
    current_user: DBUser,
    team_id: int,
    region: DBRegion,
) -> None:
    if current_user.is_admin:
        return
    if current_user.role != UserRole.TEAM_ADMIN or current_user.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )
    if not region.is_dedicated:
        return
    allowed = (
        db.query(DBUserAdminRegion)
        .filter(
            DBUserAdminRegion.user_id == current_user.id,
            DBUserAdminRegion.region_id == region.id,
        )
        .first()
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to assign this dedicated region",
        )


async def _associate_team_with_region(
    *,
    region_id: int,
    team_id: int,
    db: Session,
) -> dict[str, str]:
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

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

    # Bootstrap the team in the dedicated region's LiteLLM instance before
    # syncing members. LiteLLM requires team existence for member_add.
    # POOL teams start at $0 (purchases raise the budget).
    # PERIODIC teams start at DEFAULT_MAX_SPEND.
    max_budget = 0.0 if team.requires_pool_purchase_gate else DEFAULT_MAX_SPEND
    budget_duration = (
        f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d"
        if team.requires_pool_purchase_gate
        else None
    )
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    try:
        await litellm_service.create_team(
            team_id=lite_team_id,
            team_alias=lite_team_id,
            max_budget=max_budget,
            budget_duration=budget_duration,
        )
    except Exception as e:
        logger.error(
            "Failed to bootstrap LiteLLM team %s (db team_id=%s) in region %s: %s",
            lite_team_id,
            team_id,
            region.name,
            str(e),
        )
        try:
            persisted_association = (
                db.query(DBTeamRegion)
                .filter(
                    DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id
                )
                .first()
            )
            if persisted_association is not None:
                db.delete(persisted_association)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to rollback team-region association after LiteLLM sync failure (team_id=%s, region_id=%s)",
                team_id,
                region_id,
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to bootstrap team in LiteLLM",
        )

    try:
        team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
        for team_user in team_users:
            await sync_add_user_to_team(
                db=db,
                db_user=team_user,
                team_id=team_id,
                force_regions=[region],
            )
    except Exception as e:
        logger.error(
            "Failed to sync LiteLLM members for team %s (db team_id=%s) in region %s: %s",
            lite_team_id,
            team_id,
            region.name,
            str(e),
        )
        try:
            persisted_association = (
                db.query(DBTeamRegion)
                .filter(
                    DBTeamRegion.team_id == team_id, DBTeamRegion.region_id == region_id
                )
                .first()
            )
            if persisted_association is not None:
                db.delete(persisted_association)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to rollback team-region association after LiteLLM sync failure (team_id=%s, region_id=%s)",
                team_id,
                region_id,
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to bootstrap team in LiteLLM",
        )

    return {"message": "Team associated with region successfully"}


async def _disassociate_team_from_region(
    *,
    region_id: int,
    team_id: int,
    db: Session,
) -> dict[str, str]:
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

    region = association.region
    team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
    db.delete(association)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disassociate team from region: {str(e)}",
        )

    try:
        for team_user in team_users:
            await sync_remove_user_from_team(
                db=db,
                db_user=team_user,
                team_id=team_id,
                force_regions=[region],
            )
    except Exception as e:
        try:
            db.add(DBTeamRegion(team_id=team_id, region_id=region_id))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to restore team-region association after LiteLLM disassociation failure (team_id=%s, region_id=%s)",
                team_id,
                region_id,
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to disassociate team from LiteLLM: {str(e)}",
        )

    return {"message": "Team disassociated from region successfully"}


@router.get(
    "/teams/{team_id}/regions",
    response_model=List[RegionResponse],
    dependencies=[Depends(get_role_min_specific_team_admin)],
)
async def list_regions_for_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )
    return (
        db.query(DBRegion)
        .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
        .filter(DBTeamRegion.team_id == team_id, DBRegion.is_active.is_(True))
        .all()
    )


@router.post(
    "/teams/{team_id}/regions/{region_id}",
)
async def add_region_to_team(
    team_id: int,
    region_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    _assert_team_region_write_access(db, current_user, team_id, region)
    return await _associate_team_with_region(
        region_id=region_id, team_id=team_id, db=db
    )


@router.delete(
    "/teams/{team_id}/regions/{region_id}",
)
async def remove_region_from_team(
    team_id: int,
    region_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    _assert_team_region_write_access(db, current_user, team_id, region)
    return await _disassociate_team_from_region(
        region_id=region_id,
        team_id=team_id,
        db=db,
    )


@router.post(
    "/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)]
)
async def associate_team_with_region(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    return await _associate_team_with_region(
        region_id=region_id, team_id=team_id, db=db
    )


@router.delete(
    "/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)]
)
async def disassociate_team_from_region(
    region_id: int, team_id: int, db: Session = Depends(get_db)
):
    return await _disassociate_team_from_region(
        region_id=region_id,
        team_id=team_id,
        db=db,
    )


@router.get(
    "/{region_id}/teams",
    response_model=List[TeamSummary],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def list_teams_for_region(region_id: int, db: Session = Depends(get_db)):
    """List teams associated with a region. Only system admins can do this."""

    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )

    # Get associated teams
    teams = (
        db.query(DBTeam)
        .join(DBTeamRegion)
        .filter(DBTeamRegion.region_id == region_id)
        .all()
    )

    return teams


def _extract_region_model_names(model_info_response: dict) -> set[str]:
    data = model_info_response.get("data", model_info_response)
    if not isinstance(data, list):
        return set()
    model_names: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_name = item.get("model_name")
        if isinstance(model_name, str) and model_name:
            model_names.add(model_name)
    return model_names


def _get_dedicated_region_with_team_association_or_error(
    db: Session, region_id: int, team_id: int
) -> DBRegion:
    region = (
        db.query(DBRegion)
        .filter(DBRegion.id == region_id, DBRegion.is_active.is_(True))
        .first()
    )
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Region not found"
        )
    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model aliases are only supported for dedicated regions",
        )

    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.region_id == region_id, DBTeamRegion.team_id == team_id)
        .first()
    )
    if not association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is not associated with this dedicated region",
        )
    return region


def _assert_team_member_read_or_admin(current_user: User, team_id: int) -> None:
    if current_user.is_admin:
        return
    if current_user.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )
    if current_user.role not in UserRole.READ_ACCESS_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )


@router.get(
    "/{region_id}/teams/{team_id}/model-aliases",
    response_model=TeamRegionModelAliasesResponse,
)
async def get_team_region_model_aliases(
    region_id: int,
    team_id: int,
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    _assert_team_member_read_or_admin(current_user, team_id)
    region = _get_dedicated_region_with_team_association_or_error(
        db, region_id, team_id
    )

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    model_aliases = await service.get_team_model_aliases(lite_team_id)
    return TeamRegionModelAliasesResponse(
        region_id=region_id, team_id=team_id, model_aliases=model_aliases
    )


@router.put(
    "/{region_id}/teams/{team_id}/model-aliases",
    response_model=TeamRegionModelAliasesResponse,
    dependencies=[Depends(get_role_min_specific_team_admin)],
)
async def update_team_region_model_aliases(
    region_id: int,
    team_id: int,
    payload: TeamRegionModelAliasesUpdateRequest,
    db: Session = Depends(get_db),
):
    region = _get_dedicated_region_with_team_association_or_error(
        db, region_id, team_id
    )
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    region_model_info = await service.get_model_info()
    available_models = _extract_region_model_names(region_model_info)
    unknown_targets = sorted(
        {
            target
            for target in payload.model_aliases.values()
            if target not in available_models
        }
    )
    if unknown_targets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Alias target model not available in region catalog: "
                + ", ".join(unknown_targets)
            ),
        )

    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    team_info_response = await service.get_team_info(lite_team_id)
    team_info = team_info_response.get("team_info", team_info_response)
    current_max_budget = team_info.get("max_budget")
    current_budget_duration = team_info.get("budget_duration")
    try:
        await service.update_team_budget(
            team_id=lite_team_id,
            max_budget=current_max_budget,
            budget_duration=current_budget_duration,
            model_aliases=payload.model_aliases,
        )
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"Failed to update team model aliases: {exc.detail}",
        ) from exc

    return TeamRegionModelAliasesResponse(
        region_id=region_id,
        team_id=team_id,
        model_aliases=payload.model_aliases,
    )


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

    Data source by team type:
    - POOL teams: direct from LiteLLM team-level usage/budget (source of truth).
      If LiteLLM is unavailable, this endpoint returns 502.
      `total_budget` is reported as remaining team budget (`max_budget - spend`).
    - PERIODIC/legacy teams: existing key-level workflow with local fallback behavior.
      `total_budget` follows existing behavior as the aggregated configured key budgets.
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

    limit_service = LimitService(db)
    total_spend = 0.0
    total_budget = 0.0

    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    if team.requires_pool_purchase_gate:
        lite_team_id = LiteLLMService.format_team_id(region.name, team_id)

        try:
            team_info_response = await litellm_service.get_team_info(lite_team_id)
            team_info = team_info_response.get("team_info", team_info_response)
            total_spend = float(team_info.get("spend", 0.0) or 0.0)
            team_max_budget = float(team_info.get("max_budget", 0.0) or 0.0)
        except Exception as exc:
            logger.error(
                "Failed to get LiteLLM team info for POOL team %s in region %s: %s",
                team_id,
                region.name,
                str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to retrieve POOL team budget from LiteLLM",
            )
        total_budget = max(team_max_budget - total_spend, 0.0)

        return TeamRegionBudget(
            team_id=team_id,
            region_id=region_id,
            region_name=region.name,
            total_spend=round(total_spend, 4),
            total_budget=round(total_budget, 4),
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
