from fastapi import HTTPException, status
from typing import List
from sqlalchemy.orm import Session
from app.core.roles import UserRole
from app.db.models import DBUser, DBPrivateAIKey
import logging


class RBACDependency:
    """Base class for role-based access control dependencies"""

    logger = logging.getLogger(__name__)

    def __init__(self, allowed_roles: List[str], require_team_membership: bool = False):
        self.allowed_roles = set(allowed_roles)
        self.require_team_membership = require_team_membership

    def __call__(self, current_user: DBUser) -> str:
        return self.check_access(current_user)

    def check_access(self, user: DBUser) -> str:
        """Check if user has access and return their effective role"""
        # Validate user type constraints
        if self._validate_user_type_constraints(user):
            self.logger.info(f"User {user.id} has invalid user type constraints")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action",
            )

        # Check role permissions
        effective_role = self._get_effective_role(user)
        if effective_role not in self.allowed_roles:
            self.logger.info(f"User {user.id} has invalid role {effective_role}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action",
            )

        # Check team membership if required (but allow system admins to bypass this)
        if self.require_team_membership and not user.team_id and not user.is_admin:
            self.logger.info(f"User {user.id} is not a team member")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action",
            )

        return effective_role

    def _validate_user_type_constraints(self, user: DBUser) -> bool:
        """Validate that user type matches role constraints"""
        # System admins (is_admin=True) cannot be team members
        if user.is_admin and user.team_id is not None:
            return True

        # Get the effective role for validation
        effective_role = self._get_effective_role(user)

        # System users (team_id is None) cannot have team roles
        if user.team_id is None and effective_role in UserRole.get_team_roles():
            return True

        # Team users (team_id is not None) cannot have system roles
        if user.team_id is not None and effective_role in UserRole.get_system_roles():
            return True

        return False

    def _get_effective_role(self, user: DBUser) -> str:
        """Get the effective role for a user"""
        if user.is_admin:
            return UserRole.SYSTEM_ADMIN
        # system_admin is conferred ONLY by the is_admin flag. Never trust a
        # role column of "system_admin" on a non-admin row, or a self-registered
        # user could hold the role string and pass require_system_admin.
        if user.role == UserRole.SYSTEM_ADMIN:
            self.logger.warning(
                "User id=%s has role=system_admin but is_admin=False — "
                "downgrading to USER. Investigate for data corruption or a "
                "privilege-escalation attempt.",
                user.id,
            )
            return UserRole.USER
        return user.role or UserRole.USER


# Pre-defined dependency functions for common use cases
def require_system_admin():
    """Require system admin role"""
    return RBACDependency([UserRole.SYSTEM_ADMIN])


def require_team_admin():
    """Require team admin role or system admin"""
    return RBACDependency(UserRole.ADMIN_ROLES, require_team_membership=True)


def require_key_creator_or_higher():
    """Require key creator role or higher (team context)"""
    return RBACDependency(UserRole.KEY_MANAGEMENT_ROLES, require_team_membership=True)


def require_private_ai_access():
    """Require access to private AI operations - allows system users or team key creators"""
    return RBACDependency(
        UserRole.KEY_MANAGEMENT_ROLES + [UserRole.USER], require_team_membership=False
    )


def require_private_ai_direct_access():
    """Require access to endpoints that mint LiteLLM keys directly (no moad delegation).

    Same roles as require_private_ai_access, but require_team_membership=True so a
    self-registered, teamless USER cannot mint uncapped paid keys via /token or
    /vector-db. Teamless users go through POST / (delegated to moad, which caps
    them); system admins bypass the team check (see check_access).
    """
    return RBACDependency(
        UserRole.KEY_MANAGEMENT_ROLES + [UserRole.USER], require_team_membership=True
    )


def require_read_only_or_higher():
    """Require read only role or higher (team context)"""
    return RBACDependency(UserRole.READ_ACCESS_ROLES, require_team_membership=True)


def require_sales_or_higher():
    """Require sales role or higher (system context)"""
    return RBACDependency(UserRole.SYSTEM_ACCESS_ROLES)


def require_any_role():
    """Allow any authenticated user"""
    return RBACDependency(UserRole.get_all_roles())


# Custom role dependency creator
def require_roles(*roles: str):
    """Create a dependency that requires specific roles"""
    return RBACDependency(list(roles))


def require_roles_with_team(*roles: str):
    """Create a dependency that requires specific roles and team membership"""
    return RBACDependency(list(roles), require_team_membership=True)


def key_in_team(private_ai_key: DBPrivateAIKey, team_id: int, db: Session) -> bool:
    """Return True when a private AI key is scoped to ``team_id``.

    A key is in-team when it is directly team-owned (its ``team_id`` matches)
    or, for user-owned keys, when its owner belongs to that team. Mirrors the
    team-scoping logic already used by the private-ai-keys and spend endpoints
    so declared-scope enforcement stays consistent.
    """
    if private_ai_key.team_id is not None:
        return private_ai_key.team_id == team_id
    owner = db.query(DBUser).filter(DBUser.id == private_ai_key.owner_id).first()
    return owner is not None and owner.team_id == team_id


def enforce_declared_team_scope(
    private_ai_key: DBPrivateAIKey, declared_team_id: int | None, db: Session
) -> None:
    """Defence-in-depth scope gate for key-by-id endpoints (issue #600).

    Callers that authenticate with a shared system-admin token (notably the
    moad BFF) otherwise bypass all ownership checks, turning any endpoint keyed
    by an integer ``key_id`` into a cross-tenant IDOR. When such a caller
    declares the ``team_id`` it is acting for, the key must actually belong to
    that team — enforced here even for system admins. The failure mode is an
    indistinguishable 404 so ids cannot be enumerated.

    No-op when ``declared_team_id`` is None, so existing callers that do not
    pass a scope are unaffected (the change is backward compatible).
    """
    if declared_team_id is None:
        return
    if not key_in_team(private_ai_key, declared_team_id, db):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Private AI Key not found",
        )
