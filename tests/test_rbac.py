import pytest
from fastapi import HTTPException
from app.core.rbac import require_system_admin, require_team_admin, require_key_creator_or_higher, require_read_only_or_higher, require_sales_or_higher
from app.core.roles import UserRole
from app.db.models import DBUser

class TestRBACDependency:
    """Test RBAC dependency functionality"""

    def test_system_admin_access(self):
        """
        Given a system admin user
        When checking access to system admin endpoint
        Then access should be granted and return system_admin role
        """
        user = DBUser(id=1, email="admin@test.com", is_admin=True, team_id=None, role=None)
        dependency = require_system_admin()

        result = dependency.check_access(user)
        assert result == UserRole.SYSTEM_ADMIN

    def test_team_admin_access(self):
        """
        Given a team admin user
        When checking access to team admin endpoint
        Then access should be granted and return admin role
        """
        user = DBUser(id=1, email="teamadmin@test.com", is_admin=False, team_id=1, role="admin")
        dependency = require_team_admin()

        result = dependency.check_access(user)
        assert result == UserRole.TEAM_ADMIN

    def test_key_creator_access(self):
        """
        Given a key creator user
        When checking access to key creator endpoint
        Then access should be granted and return key_creator role
        """
        user = DBUser(id=1, email="keycreator@test.com", is_admin=False, team_id=1, role="key_creator")
        dependency = require_key_creator_or_higher()

        result = dependency.check_access(user)
        assert result == UserRole.KEY_CREATOR

    def test_read_only_access(self):
        """
        Given a read only user
        When checking access to read only endpoint
        Then access should be granted and return read_only role
        """
        user = DBUser(id=1, email="readonly@test.com", is_admin=False, team_id=1, role="read_only")
        dependency = require_read_only_or_higher()

        result = dependency.check_access(user)
        assert result == UserRole.READ_ONLY

    def test_system_user_cannot_be_team_member(self):
        """
        Given a system admin user with team_id set
        When checking access to any endpoint
        Then access should be denied due to invalid user type
        """
        user = DBUser(id=1, email="admin@test.com", is_admin=True, team_id=1, role=None)
        dependency = require_system_admin()

        with pytest.raises(HTTPException) as exc_info:
            dependency.check_access(user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    def test_team_user_must_be_team_member(self):
        """
        Given a team user without team_id
        When checking access to team endpoint
        Then access should be denied due to invalid user type
        """
        user = DBUser(id=1, email="user@test.com", is_admin=False, team_id=None, role="admin")
        dependency = require_team_admin()

        with pytest.raises(HTTPException) as exc_info:
            dependency.check_access(user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    def test_insufficient_permissions(self):
        """
        Given a read only user
        When checking access to team admin endpoint
        Then access should be denied due to insufficient permissions
        """
        user = DBUser(id=1, email="readonly@test.com", is_admin=False, team_id=1, role="read_only")
        dependency = require_team_admin()

        with pytest.raises(HTTPException) as exc_info:
            dependency.check_access(user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    def test_team_membership_required(self):
        """
        Given a system admin
        When checking access to team endpoint
        Then access should be granted because system admins can do anything
        """
        user = DBUser(id=1, email="admin@test.com", is_admin=True, team_id=None, role=None)
        dependency = require_team_admin()

        result = dependency.check_access(user)
        assert result == UserRole.SYSTEM_ADMIN

    def test_sales_user_access(self):
        """
        Given a sales user
        When checking access to sales endpoint
        Then access should be granted and return sales role
        """
        user = DBUser(id=1, email="sales@test.com", is_admin=False, team_id=None, role="sales")
        dependency = require_sales_or_higher()

        result = dependency.check_access(user)
        assert result == UserRole.SALES

    def test_system_admin_access_sales_endpoint(self):
        """
        Given a system admin user
        When checking access to sales endpoint
        Then access should be granted and return system_admin role
        """
        user = DBUser(id=1, email="admin@test.com", is_admin=True, team_id=None, role=None)
        dependency = require_sales_or_higher()

        result = dependency.check_access(user)
        assert result == UserRole.SYSTEM_ADMIN

    def test_regular_user_denied_sales_access(self):
        """
        Given a regular system user
        When checking access to sales endpoint
        Then access should be denied due to insufficient permissions
        """
        user = DBUser(id=1, email="user@test.com", is_admin=False, team_id=None, role="user")
        dependency = require_sales_or_higher()

        with pytest.raises(HTTPException) as exc_info:
            dependency.check_access(user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    def test_team_user_denied_sales_access(self):
        """
        Given a team user
        When checking access to sales endpoint
        Then access should be denied due to invalid user type
        """
        user = DBUser(id=1, email="teamuser@test.com", is_admin=False, team_id=1, role="admin")
        dependency = require_sales_or_higher()

        with pytest.raises(HTTPException) as exc_info:
            dependency.check_access(user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

class TestUserRole:
    """Test UserRole class functionality"""

    def test_system_roles(self):
        """
        Given various roles
        When checking if they are system roles
        Then only system roles should return True
        """
        assert UserRole.is_system_role("system_admin")
        assert UserRole.is_system_role("user")
        assert UserRole.is_system_role("sales")
        assert not UserRole.is_system_role("admin")
        assert not UserRole.is_system_role("key_creator")
        assert not UserRole.is_system_role("read_only")

    def test_team_roles(self):
        """
        Given various roles
        When checking if they are team roles
        Then only team roles should return True
        """
        assert UserRole.is_team_role("admin")
        assert UserRole.is_team_role("key_creator")
        assert UserRole.is_team_role("read_only")
        assert not UserRole.is_team_role("system_admin")
        assert not UserRole.is_team_role("user")
        assert not UserRole.is_team_role("sales")

    def test_get_system_roles(self):
        """
        Given the UserRole class
        When getting system roles
        Then it should return all valid system roles
        """
        roles = UserRole.get_system_roles()
        assert "system_admin" in roles
        assert "user" in roles
        assert "sales" in roles
        assert len(roles) == 3

    def test_get_team_roles(self):
        """
        Given the UserRole class
        When getting team roles
        Then it should return all valid team roles
        """
        roles = UserRole.get_team_roles()
        assert "admin" in roles
        assert "key_creator" in roles
        assert "read_only" in roles
        assert len(roles) == 3

    def test_get_all_roles(self):
        """
        Given the UserRole class
        When getting all roles
        Then it should return both system and team roles
        """
        roles = UserRole.get_all_roles()
        assert len(roles) == 6
        assert "system_admin" in roles
        assert "user" in roles
        assert "sales" in roles
        assert "admin" in roles
        assert "key_creator" in roles
        assert "read_only" in roles
