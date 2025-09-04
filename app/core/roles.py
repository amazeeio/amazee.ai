
from typing import List, Set, Literal
from enum import Enum

class UserType(Enum):
    SYSTEM = "system"
    TEAM = "team"

class UserRole:
    # System roles
    SYSTEM_ADMIN = "system_admin"
    USER = "user"  # Default system user
    SALES = "sales"

    # Team roles
    TEAM_ADMIN = "admin"
    KEY_CREATOR = "key_creator"
    READ_ONLY = "read_only"

    # Legacy support - these MUST match existing string values exactly
    ADMIN = TEAM_ADMIN  # "admin"
    DEFAULT = USER      # "user"

    @staticmethod
    def get_system_roles() -> List[str]:
        """Get all valid system user roles"""
        return [UserRole.SYSTEM_ADMIN, UserRole.USER, UserRole.SALES]

    @staticmethod
    def get_team_roles() -> List[str]:
        """Get all valid team user roles"""
        return [UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.READ_ONLY]

    @staticmethod
    def get_all_roles() -> List[str]:
        """Get all valid roles (backwards compatible)"""
        return UserRole.get_system_roles() + UserRole.get_team_roles()

    @staticmethod
    def is_system_role(role: str) -> bool:
        """Check if role is valid for system users"""
        return role in UserRole.get_system_roles()

    @staticmethod
    def is_team_role(role: str) -> bool:
        """Check if role is valid for team users"""
        return role in UserRole.get_team_roles()

    @staticmethod
    def validate_user_role_assignment(user_is_admin: bool, role: str) -> bool:
        """
        Validate if a role can be assigned to a user based on user type.

        Args:
            user_is_admin: Whether the user is a system admin
            role: The role to assign

        Returns:
            True if assignment is valid, False otherwise
        """
        if user_is_admin:
            return UserRole.is_system_role(role)
        else:
            return UserRole.is_team_role(role)

    @staticmethod
    def can_assign_role(current_role: str, target_role: str) -> bool:
        """Enhanced role assignment logic"""
        # System admins can assign any role
        if current_role == UserRole.SYSTEM_ADMIN:
            return True

        # Team admins can assign team roles within their team
        if current_role == UserRole.TEAM_ADMIN:
            return UserRole.is_team_role(target_role)

        # Other roles cannot assign roles
        return False