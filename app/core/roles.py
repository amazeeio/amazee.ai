
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

    # Role combinations for better readability
    ADMIN_ROLES = [TEAM_ADMIN, SYSTEM_ADMIN]
    KEY_MANAGEMENT_ROLES = [KEY_CREATOR] + ADMIN_ROLES
    READ_ACCESS_ROLES = [READ_ONLY] + KEY_MANAGEMENT_ROLES
    SYSTEM_ACCESS_ROLES = [SYSTEM_ADMIN, SALES]

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
