import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app.core.security import (
    check_sales_or_higher,
    get_current_user_from_auth,
    get_role_min_system_admin,
)
from app.core.roles import UserRole
from app.db.models import DBTeam, DBUser


class TestSecurityFunctions:
    """Test security function functionality"""

    @pytest.mark.asyncio
    async def test_check_sales_or_higher_sales_user(self):
        """
        Given a sales user
        When calling check_sales_or_higher
        Then it should return sales role
        """
        user = DBUser(
            id=1, email="sales@test.com", is_admin=False, team_id=None, role="sales"
        )

        result = await check_sales_or_higher(current_user=user)
        assert result == UserRole.SALES

    @pytest.mark.asyncio
    async def test_check_sales_or_higher_system_admin(self):
        """
        Given a system admin user
        When calling check_sales_or_higher
        Then it should return system_admin role
        """
        user = DBUser(
            id=1, email="admin@test.com", is_admin=True, team_id=None, role=None
        )

        result = await check_sales_or_higher(current_user=user)
        assert result == UserRole.SYSTEM_ADMIN

    @pytest.mark.asyncio
    async def test_check_sales_or_higher_regular_user_denied(self):
        """
        Given a regular system user
        When calling check_sales_or_higher
        Then it should raise HTTPException with 403 status
        """
        user = DBUser(
            id=1, email="user@test.com", is_admin=False, team_id=None, role="user"
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_sales_or_higher(current_user=user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_check_sales_or_higher_team_user_denied(self):
        """
        Given a team user
        When calling check_sales_or_higher
        Then it should raise HTTPException with 403 status
        """
        user = DBUser(
            id=1, email="teamuser@test.com", is_admin=False, team_id=1, role="admin"
        )

        with pytest.raises(HTTPException) as exc_info:
            await check_sales_or_higher(current_user=user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_check_system_admin_system_admin(self):
        """
        Given a system admin user
        When calling check_system_admin
        Then it should return system_admin role
        """
        user = DBUser(
            id=1, email="admin@test.com", is_admin=True, team_id=None, role=None
        )

        result = await get_role_min_system_admin(current_user=user)
        assert result == UserRole.SYSTEM_ADMIN

    @pytest.mark.asyncio
    async def test_check_system_admin_regular_user_denied(self):
        """
        Given a regular system user
        When calling check_system_admin
        Then it should raise HTTPException with 403 status
        """
        user = DBUser(
            id=1, email="user@test.com", is_admin=False, team_id=None, role="user"
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_role_min_system_admin(current_user=user)

        assert exc_info.value.status_code == 403
        assert "Not authorized to perform this action" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_current_user_from_auth_reloads_detached_request_user(db):
    team = DBTeam(
        name="Suspended Team",
        admin_email="suspended@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=False,
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    user = DBUser(
        email="suspended-user@example.com",
        hashed_password="hashed",
        is_active=True,
        is_admin=False,
        team_id=team.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    team.deleted_at = user.created_at
    db.commit()

    db.expunge(user)
    request = SimpleNamespace(state=SimpleNamespace(user=user))

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user_from_auth(db=db, request=request)

    assert exc_info.value.status_code == 403
    assert "suspended" in exc_info.value.detail.lower()
