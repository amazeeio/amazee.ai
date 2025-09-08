from fastapi import Depends, HTTPException, status
from typing import List, Union, Set, Callable
from app.core.roles import UserRole, UserType
from app.db.models import DBUser

class RBACDependency:
    """Base class for role-based access control dependencies"""

    def __init__(self, allowed_roles: List[str], require_team_membership: bool = False):
        self.allowed_roles = set(allowed_roles)
        self.require_team_membership = require_team_membership

    def __call__(self, current_user: DBUser) -> str:
        return self.check_access(current_user)

    def check_access(self, user: DBUser) -> str:
        """Check if user has access and return their effective role"""
        # Validate user type constraints
        if self._validate_user_type_constraints(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
            )

        # Check role permissions
        effective_role = self._get_effective_role(user)
        if effective_role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
            )

        # Check team membership if required (but allow system admins to bypass this)
        if self.require_team_membership and not user.team_id and not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action"
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
        if user.team_id is None and effective_role in ["admin", "key_creator", "read_only"]:
            return True

        # Team users (team_id is not None) cannot have system roles
        if user.team_id is not None and effective_role in ["system_admin", "user", "sales"]:
            return True

        return False

    def _get_effective_role(self, user: DBUser) -> str:
        """Get the effective role for a user"""
        if user.is_admin:
            return UserRole.SYSTEM_ADMIN
        return user.role or UserRole.USER

# Pre-defined dependency functions for common use cases
def require_system_admin():
    """Require system admin role"""
    return RBACDependency([UserRole.SYSTEM_ADMIN])

def require_team_admin():
    """Require team admin role or system admin"""
    return RBACDependency([UserRole.TEAM_ADMIN, UserRole.SYSTEM_ADMIN], require_team_membership=True)

def require_key_creator_or_higher():
    """Require key creator role or higher (team context)"""
    return RBACDependency([UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.SYSTEM_ADMIN], require_team_membership=True)

def require_private_ai_access():
    """Require access to private AI operations - allows system users or team key creators"""
    return RBACDependency([UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.SYSTEM_ADMIN, UserRole.USER], require_team_membership=False)

def require_read_only_or_higher():
    """Require read only role or higher (team context)"""
    return RBACDependency([UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.READ_ONLY, UserRole.SYSTEM_ADMIN], require_team_membership=True)

def require_sales_or_higher():
    """Require sales role or higher (system context)"""
    return RBACDependency([UserRole.SYSTEM_ADMIN, UserRole.SALES])

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
