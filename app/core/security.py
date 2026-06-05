from datetime import datetime, timedelta, UTC
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Cookie, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

from app.core.config import settings
from app.db.database import get_db
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, inspect as sa_inspect
from app.db.models import DBUser, DBAPIToken
from app.core.rbac import (
    require_system_admin,
    require_team_admin,
    require_key_creator_or_higher,
    require_sales_or_higher,
    require_private_ai_access,
)

logger = logging.getLogger(__name__)

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
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
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
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        raise credentials_exception

    email: str = payload.get("sub")
    user = (
        db.query(DBUser)
        .filter(func.lower(DBUser.email) == email.lower())
        .options(joinedload(DBUser.team))
        .first()
    )
    if user is None:
        raise credentials_exception
    return user


async def get_current_user_from_auth(
    access_token: Optional[str] = Cookie(None, alias="access_token"),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
    request: Request = None,
) -> DBUser:
    """Get current user from either JWT token (in cookie or Authorization header) or API token."""
    # First check if user is already in request state (set by AuthMiddleware)
    if request and hasattr(request.state, "user") and request.state.user is not None:
        # If we have a dict from middleware, load the full user object
        if isinstance(request.state.user, dict):
            user = _get_user_with_team(db, request.state.user["id"])
            if user:
                _check_user_team_not_suspended(user)
                return user
        else:
            # The object may be detached from its original session (e.g. loaded
            # by AuthMiddleware then committed/expired). Use SQLAlchemy inspect
            # to read the PK from the identity map without touching the DB.
            try:
                identity = sa_inspect(request.state.user).identity
                user_id = identity[0] if identity else None
            except Exception:
                user_id = None
            if user_id is not None:
                user = _get_user_with_team(db, user_id)
                if user:
                    _check_user_team_not_suspended(user)
                    return user

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

    if (
        settings.ENV_SUFFIX == "local"
        and settings.LOCAL_BEARER_TOKEN
        and token_to_try == settings.LOCAL_BEARER_TOKEN
    ):
        local_user = _get_local_bearer_user(db)
        if local_user:
            _check_user_team_not_suspended(local_user)
            return local_user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Local bearer token is configured but no active local user exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # First try API token validation since it's simpler
    try:
        db_token = (
            db.query(DBAPIToken)
            .filter(DBAPIToken.token == token_to_try)
            .options(joinedload(DBAPIToken.owner).joinedload(DBUser.team))
            .first()
        )
        if db_token:
            # Update last used timestamp
            db_token.last_used_at = datetime.now(UTC)
            _check_user_team_not_suspended(db_token.owner)
            db.commit()
            return db_token.owner
    except HTTPException:
        raise
    except Exception:
        pass

    # If API token validation fails, try JWT validation
    try:
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=token_to_try
        )
        user = await get_current_user(credentials=credentials, db=db)
        if user:
            _check_user_team_not_suspended(user)
            return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _check_user_team_not_suspended(user: DBUser) -> None:
    """Raise 403 if the user's team has been soft-deleted.

    System admins are excluded from this check so they retain access to the
    admin interface even when their own team is soft-deleted.
    """
    if (
        not user.is_admin
        and user.team_id is not None
        and user.team is not None
        and user.team.deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your organization has been suspended. Please contact support.",
        )


def _get_user_with_team(db: Session, user_id: int) -> Optional[DBUser]:
    return (
        db.query(DBUser)
        .filter(DBUser.id == user_id)
        .options(joinedload(DBUser.team))
        .first()
    )


def _get_local_bearer_user(db: Session) -> Optional[DBUser]:
    if settings.LOCAL_BEARER_USER_EMAIL:
        preferred_user = (
            db.query(DBUser)
            .filter(
                func.lower(DBUser.email) == settings.LOCAL_BEARER_USER_EMAIL.lower(),
                DBUser.is_active.is_(True),
            )
            .options(joinedload(DBUser.team))
            .first()
        )
        if preferred_user:
            return preferred_user

    admin_user = (
        db.query(DBUser)
        .filter(DBUser.is_admin.is_(True), DBUser.is_active.is_(True))
        .options(joinedload(DBUser.team))
        .first()
    )
    if admin_user:
        return admin_user

    return (
        db.query(DBUser)
        .filter(DBUser.is_active.is_(True))
        .options(joinedload(DBUser.team))
        .first()
    )


async def get_role_min_system_admin(
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """Check if the current user is a system admin."""
    dependency = require_system_admin()
    return dependency.check_access(current_user)


async def get_role_min_team_admin(
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """Require team admin role or higher."""
    dependency = require_team_admin()
    return dependency.check_access(current_user)


async def get_role_min_specific_team_admin(
    current_user: DBUser = Depends(get_current_user_from_auth), team_id: int = None
):
    """Check if user is admin of specific team."""
    dependency = require_team_admin()
    role = dependency.check_access(current_user)

    # Additional team-specific check
    if not current_user.is_admin and not current_user.team_id == team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )
    return role


async def get_role_min_key_creator(
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """Require key creator role or higher."""
    dependency = require_key_creator_or_higher()
    return dependency.check_access(current_user)


async def get_private_ai_access(
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """Require access to private AI operations - allows system users or team key creators."""
    dependency = require_private_ai_access()
    return dependency.check_access(current_user)


async def check_sales_or_higher(
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """Check if the current user is a sales user or system admin."""
    dependency = require_sales_or_higher()
    return dependency.check_access(current_user)
