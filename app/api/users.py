from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Literal

from app.db.database import get_db
from app.schemas.models import User, UserUpdate, UserCreate, TeamOperation, UserRoleUpdate
from app.db.models import DBUser, DBTeam, DBAuditLog
from app.api.auth import get_current_user_from_auth
from app.core.security import get_password_hash, check_system_admin, check_team_admin
from datetime import datetime, UTC

router = APIRouter()

@router.get("/search", response_model=List[User], dependencies=[Depends(check_system_admin)])
async def search_users(
    email: str,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Search users by email pattern. Only accessible by admin users.
    Returns a list of users whose email matches the search pattern.
    """
    users = db.query(DBUser).filter(DBUser.email.ilike(f"%{email}%")).limit(10).all()
    return users

@router.get("", response_model=List[User], dependencies=[Depends(check_team_admin)])
@router.get("/", response_model=List[User], dependencies=[Depends(check_team_admin)])
async def list_users(
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    List users. Accessible by admin users or team admins for their team members.
    """
    if current_user.is_admin:
        users = db.query(DBUser).all()
        # Add team information to each user
        for user in users:
            if user.team_id:
                team = db.query(DBTeam).filter(DBTeam.id == user.team_id).first()
                if team:
                    user.team_name = team.name
            else:
                user.team_name = None
        return users

    # Return only users in the team admin's team
    users = db.query(DBUser).filter(DBUser.team_id == current_user.team_id).all()
    # Add team information to each user
    for user in users:
        if user.team_id:
            team = db.query(DBTeam).filter(DBTeam.id == user.team_id).first()
            if team:
                user.team_name = team.name
        else:
            user.team_name = None
    return users

@router.post("", response_model=User, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_team_admin)])
@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_team_admin)])
async def create_user(
    user: UserCreate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Create a new user. Accessible by admin users or team admins for their own team.
    """
    # Check if email already exists
    db_user = db.query(DBUser).filter(DBUser.email == user.email).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Admin may only create a user in their own team.
    if current_user.team_id != user.team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

    # Create the user
    hashed_password = get_password_hash(user.password)
    db_user = DBUser(
        email=user.email,
        hashed_password=hashed_password,
        is_admin=False,  # Users are created as non-admin by default
        team_id=user.team_id
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return db_user

@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()

    # If user doesn't exist, return 404
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Allow admin users to view any user
    if current_user.is_admin:
        return db_user

    # Allow team admins to view users in their own team
    if current_user.team_id is not None and current_user.team_id == db_user.team_id and current_user.role == "admin":
        return db_user

    # Otherwise, return 404 to avoid leaking information about user existence
    raise HTTPException(status_code=404, detail="User not found")

@router.put("/{user_id}", response_model=User, dependencies=[Depends(check_team_admin)])
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Update a user. Accessible by admin users or team admins.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if trying to make a team member an admin
    if user_update.is_admin is True and db_user.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team members cannot be made administrators"
        )

    for key, value in user_update.model_dump(exclude_unset=True).items():
        setattr(db_user, key, value)

    db.commit()
    db.refresh(db_user)
    return db_user

@router.post("/{user_id}/add-to-team", response_model=User, dependencies=[Depends(check_team_admin)])
async def add_user_to_team(
    user_id: int,
    team_operation: TeamOperation,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Add a user to a team. Accessible by admin users or team admins.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is already a member of another team
    if db_user.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of another team"
        )

    # Check if trying to add an admin to a team
    if db_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot be added to teams"
        )

    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_operation.team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Add user to team
    db_user.team_id = team_operation.team_id
    db.commit()
    db.refresh(db_user)
    return db_user

@router.post("/{user_id}/remove-from-team", response_model=User, dependencies=[Depends(check_system_admin)])
async def remove_user_from_team(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Remove a user from a team. Accessible by admin users.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is a member of a team
    if db_user.team_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a member of any team"
        )

    # Remove user from team
    db_user.team_id = None
    db.commit()
    db.refresh(db_user)
    return db_user

@router.delete("/{user_id}", dependencies=[Depends(check_system_admin)])
async def delete_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has associated AI keys
    if db_user.private_ai_keys and len(db_user.private_ai_keys) > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete user with associated AI keys"
        )

    db.delete(db_user)
    db.commit()
    return {"message": "User deleted successfully"}

@router.post("/{user_id}/role", response_model=User)
async def update_user_role(
    user_id: int,
    role_update: UserRoleUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Update a user's role. Accessible by admin users or team admins for their team members.
    """
    # Validate role
    valid_roles = Literal["admin", "key_creator", "read_only"]
    if role_update.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}"
        )

    # Get the user to update
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check authorization
    if not current_user.is_admin:
        # If not a system admin, check if user is a team admin
        if not current_user.team_id or current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
            )

        # If team admin, ensure they're updating a user in their own team
        if db_user.team_id != current_user.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Team admins can only update roles for users in their own team"
            )

    # Don't allow changing admin roles through this endpoint
    if db_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change role of an administrator"
        )

    # Update the role
    old_role = db_user.role
    db_user.role = role_update.role
    db_user.updated_at = datetime.now(UTC)

    # Create audit log
    audit_log = DBAuditLog(
        user_id=current_user.id,
        event_type="user_role_update",
        resource_type="user",
        resource_id=str(user_id),
        action="update",
        details={
            "old_role": old_role,
            "new_role": role_update.role,
            "user_email": db_user.email
        }
    )
    db.add(audit_log)

    db.commit()
    db.refresh(db_user)
    return db_user
