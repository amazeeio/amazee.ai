from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Response, Cookie, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.models import Token, User, UserCreate
from app.db.models import DBUser, DBAPIToken
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
)
from app.core.config import settings

router = APIRouter()

def authenticate_user(db: Session, email: str, password: str) -> Optional[DBUser]:
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user

@router.post("/login", response_model=Token)
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    access_token = create_access_token(
        data={"sub": user.email}
    )

    # Set cookie
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        max_age=1800,
        expires=1800,
    )

    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key="access_token",
        path="/"
    )
    return {"message": "Successfully logged out"}

async def get_current_user_from_token(
    api_token: str = Header(None, alias="X-API-Token"),
    db: Session = Depends(get_db)
) -> DBUser:
    if not api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token is missing",
        )

    db_token = db.query(DBAPIToken).filter(DBAPIToken.token == api_token).first()
    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )

    # Update last used timestamp
    db_token.last_used_at = datetime.utcnow()
    db.commit()

    return db_token.user

async def get_current_user_from_auth(
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    api_token: Optional[str] = Header(None, alias="X-API-Token"),
    db: Session = Depends(get_db)
) -> DBUser:
    if api_token:
        return await get_current_user_from_token(api_token, db)

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Remove "Bearer " prefix if present
    if access_token.startswith("Bearer "):
        access_token = access_token[7:]

    return await get_current_user(token=access_token, db=db)

@router.get("/me", response_model=User)
async def read_users_me(current_user: DBUser = Depends(get_current_user_from_auth)):
    return current_user

@router.post("/register", response_model=User)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user with this email exists
    db_user = db.query(DBUser).filter(DBUser.email == user.email).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create new user
    hashed_password = get_password_hash(user.password)
    db_user = DBUser(
        email=user.email,
        hashed_password=hashed_password,
        is_admin=user.is_admin
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user