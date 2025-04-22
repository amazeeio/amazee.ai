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
    db_team = db.query(DBTeam).filter(DBTeam.email == team.email).first()
    if db_team:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create the team
    db_team = DBTeam(
        name=team.name,
        email=team.email,
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
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    List all teams. Only accessible by admin users.
    """
    return db.query(DBTeam).all()

@router.get("/{team_id}", response_model=TeamWithUsers, dependencies=[Depends(check_specific_team_admin)])
async def get_team(
    team_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Get a team by ID. Accessible by admin users or users associated with the team.
    """
    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Get users associated with the team
    team_users = []
    for user in db_team.users:
        team_users.append({
            "id": user.id,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "role": user.role
        })

    # Create response with users
    response = TeamWithUsers(
        id=db_team.id,
        name=db_team.name,
        email=db_team.email,
        phone=db_team.phone,
        billing_address=db_team.billing_address,
        is_active=db_team.is_active,
        created_at=db_team.created_at,
        updated_at=db_team.updated_at,
        users=team_users
    )

    return response

@router.put("/{team_id}", response_model=Team, dependencies=[Depends(check_specific_team_admin)])
async def update_team(
    team_id: int,
    team_update: TeamUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
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
    current_user: DBUser = Depends(get_current_user_from_auth),
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