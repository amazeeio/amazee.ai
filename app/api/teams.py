from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, UTC
import logging

from app.db.database import get_db
from app.db.models import DBTeam, DBTeamProduct, DBUser
from app.core.security import check_system_admin, check_specific_team_admin, get_current_user_from_auth
from app.schemas.models import (
    Team, TeamCreate, TeamUpdate,
    TeamWithUsers
)
from app.core.resource_limits import DEFAULT_KEY_DURATION, DEFAULT_MAX_SPEND, DEFAULT_RPM_PER_KEY
from app.services.litellm import LiteLLMService
from app.services.ses import SESService
from app.core.worker import get_team_keys_by_region, generate_pricing_url, get_team_admin_email

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
    # Check if team email already exists
    db_team = db.query(DBTeam).filter(DBTeam.admin_email == team.admin_email).first()
    if db_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
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

@router.get("", response_model=List[Team], dependencies=[Depends(check_system_admin)])
@router.get("/", response_model=List[Team], dependencies=[Depends(check_system_admin)])
async def list_teams(
    db: Session = Depends(get_db)
):
    """
    List all teams. Only accessible by admin users.
    """
    return db.query(DBTeam).all()

@router.get("/{team_id}", response_model=TeamWithUsers, dependencies=[Depends(check_specific_team_admin)])
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

@router.put("/{team_id}", response_model=Team, dependencies=[Depends(check_specific_team_admin)])
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

@router.delete("/{team_id}", dependencies=[Depends(check_system_admin)])
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

@router.post("/{team_id}/extend-trial", dependencies=[Depends(check_system_admin)])
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
                continue

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
