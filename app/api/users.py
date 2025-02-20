from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.schemas.models import User, UserUpdate, UserCreate
from app.db.models import DBUser
from app.api.auth import get_current_user_from_auth
from app.core.security import get_password_hash

router = APIRouter()

def check_admin(current_user: DBUser):
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

@router.get("/search", response_model=List[User])
async def search_users(
    email: str,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Search users by email pattern. Only accessible by admin users.
    Returns a list of users whose email matches the search pattern.
    """
    check_admin(current_user)
    users = db.query(DBUser).filter(DBUser.email.ilike(f"%{email}%")).limit(10).all()
    return users

@router.get("", response_model=List[User])
@router.get("/", response_model=List[User])
async def list_users(
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    return db.query(DBUser).all()

@router.post("", response_model=User, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=User, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UserCreate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    db_user = db.query(DBUser).filter(DBUser.email == user.email).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    hashed_password = get_password_hash(user.password)
    db_user = DBUser(
        email=user.email,
        hashed_password=hashed_password,
        is_admin=False
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.get("/me", response_model=User)
async def read_users_me(current_user: DBUser = Depends(get_current_user_from_auth)):
    return current_user

@router.put("/me", response_model=User)
async def update_user_me(
    user_update: UserUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    db_user = db.query(DBUser).filter(DBUser.id == current_user.id).first()
    for key, value in user_update.model_dump(exclude_unset=True).items():
        if key == "is_admin" and not current_user.is_admin:
            continue  # Only admins can change admin status
        setattr(db_user, key, value)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user

@router.put("/{user_id}", response_model=User)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    for key, value in user_update.model_dump(exclude_unset=True).items():
        setattr(db_user, key, value)

    db.commit()
    db.refresh(db_user)
    return db_user

@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    check_admin(current_user)
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(db_user)
    db.commit()
    return {"message": "User deleted successfully"}