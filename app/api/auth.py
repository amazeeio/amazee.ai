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

router = APIRouter(
    tags=["Authentication"]
)

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
    """
    Login to get access to the API.

    - **username**: Your email address
    - **password**: Your password

    On successful login, an access token will be set as an HTTP-only cookie and also returned in the response.
    Use this token for subsequent authenticated requests.
    """
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
    authorization: str = Header(None),
    db: Session = Depends(get_db)
) -> DBUser:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is missing",
        )

    # Extract token from Authorization header
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Use 'Bearer <token>'",
        )

    api_token = parts[1]
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
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> DBUser:
    if not access_token and not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Try JWT token first
    token_to_try = access_token
    if authorization:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format. Use 'Bearer <token>'",
            )
        token_to_try = parts[1]

    try:
        # Try JWT token validation first
        return await get_current_user(token=token_to_try, db=db)
    except HTTPException as jwt_error:
        # If JWT validation fails, try API token validation
        try:
            db_token = db.query(DBAPIToken).filter(DBAPIToken.token == token_to_try).first()
            if not db_token:
                raise jwt_error

            # Update last used timestamp
            db_token.last_used_at = datetime.utcnow()
            db.commit()

            return db_token.user
        except Exception:
            raise jwt_error

@router.get("/me", response_model=User)
async def read_users_me(current_user: DBUser = Depends(get_current_user_from_auth)):
    return current_user

@router.post("/register", response_model=User)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user account.

    - **email**: Your email address
    - **password**: A secure password (minimum 8 characters)

    After registration, you'll need to login to get an access token.
    """
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
        is_admin=False  # Force is_admin to be False for all new registrations
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user