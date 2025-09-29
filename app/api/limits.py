from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.dependencies import get_limit_service
from app.schemas.limits import (
    TeamLimits,
    LimitedResource,
    OverwriteLimitRequest,
    ResetLimitRequest,
    LimitSource,
)
from app.core.limit_service import LimitService, LimitNotFoundError
from app.core.security import get_role_min_system_admin, get_current_user_from_auth
from app.db.models import DBUser, DBTeam
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["limits"]
)


@router.get("/teams/{team_id}", response_model=TeamLimits, dependencies=[Depends(get_role_min_system_admin)])
async def get_team_limits(
    team_id: int,
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service)
):
    """
    Get all effective limits for a team.
    Only accessible by system administrators.
    """
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    try:
        return limit_service.get_team_limits(team)
    except Exception as e:
        logger.error(f"Error getting team limits for team {team_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve team limits"
        )


@router.get("/users/{user_id}", response_model=TeamLimits, dependencies=[Depends(get_role_min_system_admin)])
async def get_user_limits(
    user_id: int,
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service)
):
    """
    Get all effective limits for a user.
    Users inherit team limits unless they have individual overrides.
    Only accessible by system administrators.
    """
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"

        )
    try:
        return limit_service.get_user_limits(user)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user limits for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user limits"
        )


@router.put("/overwrite", response_model=LimitedResource, dependencies=[Depends(get_role_min_system_admin)])
async def overwrite_limit(
    request: OverwriteLimitRequest,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service)
):
    """
    Create or update a limit (always creates MANUAL limits when set via API).

    When limits are set via API by administrators, they are automatically
    treated as MANUAL limits since they're being set by a person.

    Only accessible by system administrators.
    """
    try:
        result = limit_service.set_limit(
            owner_type=request.owner_type,
            owner_id=request.owner_id,
            resource_type=request.resource_type,
            limit_type=request.limit_type,
            unit=request.unit,
            max_value=request.max_value,
            current_value=request.current_value,
            limited_by=LimitSource.MANUAL,  # Always MANUAL when set via API
            set_by=current_user.email  # Use the admin's email who made the change
        )
        return LimitedResource.model_validate(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error overwriting limit: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to overwrite limit"
        )


@router.post("/teams/{team_id}/reset", response_model=TeamLimits, dependencies=[Depends(get_role_min_system_admin)])
async def reset_team_limits(
    team_id: int,
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service)
):
    """
    Reset all limits for a team following cascade rules.
    MANUAL -> PRODUCT -> DEFAULT based on availability.

    Only accessible by system administrators.
    """
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
    try:
        return limit_service.reset_team_limits(team)
    except Exception as e:
        logger.error(f"Error resetting team limits for team {team_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset team limits"
        )


@router.post("/reset", response_model=LimitedResource, dependencies=[Depends(get_role_min_system_admin)])
async def reset_limit(
    request: ResetLimitRequest,
    db: Session = Depends(get_db),
    limit_service: LimitService = Depends(get_limit_service)
):
    """
    Reset a specific limit following cascade rules.
    MANUAL -> PRODUCT -> DEFAULT based on availability.

    Only accessible by system administrators.
    """
    try:
        result = limit_service.reset_limit(
            request.owner_type,
            request.owner_id,
            request.resource_type
        )
        return LimitedResource.model_validate(result)
    except HTTPException:
        raise
    except LimitNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error resetting limit: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset limit"
        )
