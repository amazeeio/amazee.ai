from datetime import datetime, timedelta, UTC
from typing import Optional, Literal, Dict
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Cookie, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging
from app.core.config import settings
from app.db.database import get_db
from sqlalchemy.orm import Session
from app.db.models import DBUser, DBAPIToken

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Custom bearer scheme
bearer_scheme = HTTPBearer(auto_error=False)

# Define valid user roles as a Literal type
UserRole = Literal["admin", "key_creator", "read_only", "user", "system_admin"]

# Define a hierarchy for roles
user_role_hierarchy: Dict[UserRole, int] = {
    "admin": 0,
    "user": 1,
    "key_creator": 2,
    "read_only": 3,
    "system_admin": 4,
}

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
    except JWTError:
        raise credentials_exception

    email: str = payload.get("sub")
    user = db.query(DBUser).filter(DBUser.email == email).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_user_from_auth(
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    request: Request = None
) -> DBUser:
    """Get current user from either JWT token (in cookie or Authorization header) or API token."""
    # First check if user is already in request state (set by AuthMiddleware)
    if request and hasattr(request.state, 'user') and request.state.user is not None:
        # If we have a dict from middleware, load the full user object
        if isinstance(request.state.user, dict):
            user = db.query(DBUser).filter(DBUser.id == request.state.user["id"]).first()
            if user:
                return user
        else:
            return request.state.user

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

def get_user_role(minimum_role: UserRole, current_user: DBUser):
    if current_user.is_admin:
        return "system_admin"
    elif user_role_hierarchy[current_user.role] > user_role_hierarchy[minimum_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )
    return current_user.role

async def get_role_min_team_admin(current_user: DBUser = Depends(get_current_user_from_auth)):
    return get_user_role("admin", current_user)

async def check_specific_team_admin(current_user: DBUser = Depends(get_current_user_from_auth), team_id: int = None):
    get_user_role("admin", current_user)
    # system administrators will fail the team check
    if not current_user.is_admin and not current_user.team_id == team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action"
        )

async def get_role_min_key_creator(current_user: DBUser = Depends(get_current_user_from_auth)):
    return get_user_role("key_creator", current_user)
