from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import requests
import asyncpg
import logging

from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import Region, RegionCreate, RegionResponse, User, RegionUpdate, TeamSummary
from app.db.models import DBRegion, DBPrivateAIKey, DBTeamRegion, DBTeam
from app.core.security import get_role_min_system_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["regions"]
)

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
            timeout=10
        )
        response.raise_for_status()
        logger.info(f"LiteLLM endpoint validation successful for {api_url}")
        return True
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                error_msg = f"Status {e.response.status_code}: {error_details}"
            except ValueError:
                error_msg = f"Status {e.response.status_code}: {e.response.text}"
        logger.error(f"LiteLLM endpoint validation failed for {api_url}: {error_msg}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"LiteLLM endpoint validation failed: {error_msg}"
        )

async def validate_database_connection(host: str, port: int, user: str, password: str) -> bool:
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
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password
        )
        await conn.close()
        logger.info(f"Database connection validation successful for {host}:{port}")
        return True
    except asyncpg.exceptions.PostgresError as e:
        logger.error(f"Database connection validation failed for {host}:{port}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database connection validation failed: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Unexpected error during database validation for {host}:{port}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database connection validation failed: {str(e)}"
        )

@router.post("", response_model=Region, dependencies=[Depends(get_role_min_system_admin)])
@router.post("/", response_model=Region, dependencies=[Depends(get_role_min_system_admin)])
async def create_region(
    region: RegionCreate,
    db: Session = Depends(get_db)
):
    # Check if region with this name already exists
    existing_region = db.query(DBRegion).filter(DBRegion.name == region.name).first()
    if existing_region:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A region with the name '{region.name}' already exists"
        )

    # Validate LiteLLM endpoint
    await validate_litellm_endpoint(region.litellm_api_url, region.litellm_api_key)

    # Validate database connection
    await validate_database_connection(
        region.postgres_host,
        region.postgres_port,
        region.postgres_admin_user,
        region.postgres_admin_password
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
            detail=f"Failed to create region: {str(e)}"
        )
    return db_region

@router.get("", response_model=List[RegionResponse])
@router.get("/", response_model=List[RegionResponse])
async def list_regions(
    current_user: User = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    # System admin users can see all regions
    if current_user.is_admin:
        return db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()

    # Regular users can only see non-dedicated regions
    if not current_user.team_id:
        return db.query(DBRegion).filter(
            DBRegion.is_active.is_(True),
            DBRegion.is_dedicated.is_(False)
        ).all()

    # Team members can see non-dedicated regions plus their team's dedicated regions
    team_dedicated_regions = db.query(DBRegion).join(DBTeamRegion).filter(
        DBRegion.is_active.is_(True),
        DBRegion.is_dedicated.is_(True),
        DBTeamRegion.team_id == current_user.team_id
    ).all()

    non_dedicated_regions = db.query(DBRegion).filter(
        DBRegion.is_active.is_(True),
        DBRegion.is_dedicated.is_(False)
    ).all()

    return non_dedicated_regions + team_dedicated_regions

@router.get("/admin", response_model=List[Region], dependencies=[Depends(get_role_min_system_admin)])
async def list_admin_regions(
    db: Session = Depends(get_db)
):
    return db.query(DBRegion).all()

@router.get("/{region_id}", response_model=RegionResponse, dependencies=[Depends(get_role_min_system_admin)])
async def get_region(
    region_id: int,
    db: Session = Depends(get_db)
):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )
    return region

@router.delete("/{region_id}", dependencies=[Depends(get_role_min_system_admin)])
async def delete_region(
    region_id: int,
    db: Session = Depends(get_db)
):
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Check if there are any keys using this region
    existing_keys = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.region_id == region_id).count()
    if existing_keys > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete region: {existing_keys} keys(s) are currently using this region. Please delete these keys first."
        )

    # Instead of deleting, mark as inactive
    region.is_active = False
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to delete region: {str(e)}"
        )
    return {"message": "Region deleted successfully"}

@router.put("/{region_id}", response_model=Region, dependencies=[Depends(get_role_min_system_admin)])
async def update_region(
    region_id: int,
    region: RegionUpdate,
    db: Session = Depends(get_db)
):

    db_region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not db_region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    # Check if updating to a name that already exists (excluding current region)
    if region.name != db_region.name:
        existing_region = db.query(DBRegion).filter(
            DBRegion.name == region.name,
            DBRegion.id != region_id
        ).first()
        if existing_region:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A region with the name '{region.name}' already exists"
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
            detail=f"Failed to update region: {str(e)}"
        )
    return db_region

@router.post("/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)])
async def associate_team_with_region(
    region_id: int,
    team_id: int,
    db: Session = Depends(get_db)
):
    """Associate a team with a dedicated region. Only system admins can do this."""

    # Check if region exists and is dedicated
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only associate teams with dedicated regions"
        )

    # Check if team exists
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    # Check if association already exists
    existing_association = db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == team_id,
        DBTeamRegion.region_id == region_id
    ).first()

    if existing_association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is already associated with this region"
        )

    # Create the association
    team_region = DBTeamRegion(
        team_id=team_id,
        region_id=region_id
    )
    db.add(team_region)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to associate team with region: {str(e)}"
        )

    return {"message": "Team associated with region successfully"}

@router.delete("/{region_id}/teams/{team_id}", dependencies=[Depends(get_role_min_system_admin)])
async def disassociate_team_from_region(
    region_id: int,
    team_id: int,
    db: Session = Depends(get_db)
):
    """Disassociate a team from a dedicated region. Only system admins can do this."""

    # Check if association exists
    association = db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == team_id,
        DBTeamRegion.region_id == region_id
    ).first()

    if not association:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team-region association not found"
        )

    # Remove the association
    db.delete(association)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disassociate team from region: {str(e)}"
        )

    return {"message": "Team disassociated from region successfully"}

@router.get("/{region_id}/teams", response_model=List[TeamSummary], dependencies=[Depends(get_role_min_system_admin)])
async def list_teams_for_region(
    region_id: int,
    db: Session = Depends(get_db)
):
    """List teams associated with a dedicated region. Only system admins can do this."""

    # Check if region exists and is dedicated
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Region not found"
        )

    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only list teams for dedicated regions"
        )

    # Get associated teams
    teams = db.query(DBTeam).join(DBTeamRegion).filter(
        DBTeamRegion.region_id == region_id
    ).all()

    return teams