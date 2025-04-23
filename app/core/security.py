from datetime import datetime, timedelta, UTC
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Cookie, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.db.database import get_db
from sqlalchemy.orm import Session
from app.db.models import DBUser, DBAPIToken

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Custom bearer scheme
bearer_scheme = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Generate password hash."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=60)  # Default to 60 minutes
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db)
):
    """Get current user from JWT token."""
    if not credentials:
        return None

    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_user_from_auth(
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> DBUser:
    """Get current user from either JWT token (in cookie or Authorization header) or API token."""
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

    # First try API token validation since it's simpler
    try:
        db_token = db.query(DBAPIToken).filter(DBAPIToken.token == token_to_try).first()
        if db_token:
            # Update last used timestamp
            db_token.last_used_at = datetime.now(UTC)
            db.commit()
            return db_token.owner
    except Exception:
        pass

    # If API token validation fails, try JWT validation
    try:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_to_try)
        user = await get_current_user(credentials=credentials, db=db)
        if user:
            return user
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def check_system_admin(current_user: DBUser = Depends(get_current_user_from_auth)):
    """Check if the current user is a system admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

async def check_team_admin(current_user: DBUser = Depends(get_current_user_from_auth)):
    """Check if the current user is an admin of a team"""
    # System admin overrides all
    if (not current_user.is_admin) and (not (current_user.team_id and current_user.role == "admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

async def check_specific_team_admin(current_user: DBUser = Depends(get_current_user_from_auth), team_id: int = None):
    # System admin overrides all
    if (not current_user.is_admin) and (not (current_user.team_id == team_id and current_user.role == "admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )