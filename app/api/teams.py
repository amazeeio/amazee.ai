from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBTeam, DBUser
from app.schemas.models import (
    Team, TeamCreate, TeamUpdate,
    TeamWithUsers
)
from app.api.auth import get_current_user_from_auth

router = APIRouter()

def check_admin(current_user: DBUser):
    """Check if the current user is an admin"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

def check_team_admin(current_user: DBUser, team_id: int, db: Session):
    """Check if the current user is an admin of the specified team"""
    if current_user.is_admin:
        return True

    # Check if user is associated with the team and has admin role
    if current_user.team_id != team_id or current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action for this team"
        )

    return True

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

@router.get("", response_model=List[Team])
@router.get("/", response_model=List[Team])
async def list_teams(
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    List all teams. Only accessible by admin users.
    """
    check_admin(current_user)
    return db.query(DBTeam).all()

@router.get("/{team_id}", response_model=TeamWithUsers)
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

    # Check if user is authorized
    if not current_user.is_admin and current_user.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this team"
        )

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

@router.put("/{team_id}", response_model=Team)
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

    # Check if user is authorized
    check_team_admin(current_user, team_id, db)

    # Update team fields
    for key, value in team_update.model_dump(exclude_unset=True).items():
        setattr(db_team, key, value)

    db_team.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_team)

    return db_team

@router.delete("/{team_id}")
async def delete_team(
    team_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Delete a team. Only accessible by admin users.
    """
    check_admin(current_user)

    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Delete the team
    db.delete(db_team)
    db.commit()

    return {"message": "Team deleted successfully"}