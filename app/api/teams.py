from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBTeam, DBUser
from app.core.security import check_system_admin, check_specific_team_admin
from app.schemas.models import (
    Team, TeamCreate, TeamUpdate,
    TeamWithUsers
)
from app.api.auth import get_current_user_from_auth

router = APIRouter()

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
    db: Session = Depends(get_db)
):
    """
    Update a team. Accessible by admin users or team admins.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Update team fields
    for key, value in team_update.model_dump(exclude_unset=True).items():
        setattr(db_team, key, value)

    db_team.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_team)

    return db_team

@router.delete("/{team_id}", dependencies=[Depends(check_system_admin)])
async def delete_team(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a team. Only accessible by admin users.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Delete the team
    db.delete(db_team)
    db.commit()

    return {"message": "Team deleted successfully"}