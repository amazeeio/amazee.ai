from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Response, Cookie, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import logging
import secrets

logger = logging.getLogger(__name__)

from app.db.database import get_db
from app.schemas.models import Token, User, UserCreate, APIToken, APITokenCreate
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

    # Set cookie with more permissive settings
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=False,  # Allow JavaScript access
        max_age=1800,
        expires=1800,
        samesite='none',  # Allow cross-site requests
        secure=True,     # Still require HTTPS for security
        path='/',        # Make cookie available for all paths
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
        logger.debug("No access token or authorization header found")
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
            logger.debug("Invalid authorization header format")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format. Use 'Bearer <token>'",
            )
        token_to_try = parts[1]
        logger.debug("Using token from authorization header")
    else:
        logger.debug("Using token from cookie")

    try:
        # Create HTTPAuthorizationCredentials for the token
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_to_try)
        # Try JWT token validation first
        user = await get_current_user(credentials=credentials, db=db)
        if user:
            logger.debug(f"Successfully authenticated user {user.id} using JWT token")
            return user
    except HTTPException as jwt_error:
        # If JWT validation fails, try API token validation
        try:
            logger.debug("JWT validation failed, trying API token")
            db_token = db.query(DBAPIToken).filter(DBAPIToken.token == token_to_try).first()
            if not db_token:
                logger.debug("No valid API token found")
                raise jwt_error

            # Update last used timestamp
            db_token.last_used_at = datetime.utcnow()
            db.commit()

            logger.debug(f"Successfully authenticated user {db_token.user.id} using API token")
            return db_token.user
        except Exception:
            logger.debug("API token validation failed")
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

def generate_token() -> str:
    return secrets.token_urlsafe(32)

# API Token routes
@router.post("/token", response_model=APIToken)
async def create_token(
    token_create: APITokenCreate,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    db_token = DBAPIToken(
        name=token_create.name,
        token=generate_token(),
        user_id=current_user.id
    )
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    return db_token

@router.get("/token", response_model=List[APIToken])
async def list_tokens(
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    return current_user.api_tokens

@router.delete("/token/{token_id}")
async def delete_token(
    token_id: int,
    current_user = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    token = db.query(DBAPIToken).filter(
        DBAPIToken.id == token_id,
        DBAPIToken.user_id == current_user.id
    ).first()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found"
        )

    db.delete(token)
    db.commit()
    return {"message": "Token deleted successfully"}