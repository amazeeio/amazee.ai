import os
import subprocess
import sys

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app.core.security import (
    check_sales_or_higher,
    get_current_user_from_auth,
    get_role_min_system_admin,
)
from app.core.config import settings
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


@pytest.mark.asyncio
async def test_get_current_user_from_auth_accepts_local_bearer(db):
    admin = DBUser(
        email="local-admin@example.com",
        hashed_password="hashed",
        is_active=True,
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    old_env_suffix = settings.ENV_SUFFIX
    old_local_token = settings.LOCAL_BEARER_TOKEN
    old_local_email = settings.LOCAL_BEARER_USER_EMAIL
    settings.ENV_SUFFIX = "local"
    settings.LOCAL_BEARER_TOKEN = "LOCALBT"
    settings.LOCAL_BEARER_USER_EMAIL = ""
    try:
        user = await get_current_user_from_auth(
            authorization="Bearer LOCALBT",
            db=db,
        )
    finally:
        settings.ENV_SUFFIX = old_env_suffix
        settings.LOCAL_BEARER_TOKEN = old_local_token
        settings.LOCAL_BEARER_USER_EMAIL = old_local_email

    assert user.id == admin.id


@pytest.mark.asyncio
async def test_get_current_user_from_auth_does_not_accept_local_bearer_from_cookie(db):
    admin = DBUser(
        email="local-cookie-admin@example.com",
        hashed_password="hashed",
        is_active=True,
        is_admin=True,
    )
    db.add(admin)
    db.commit()

    old_env_suffix = settings.ENV_SUFFIX
    old_local_token = settings.LOCAL_BEARER_TOKEN
    old_local_email = settings.LOCAL_BEARER_USER_EMAIL
    settings.ENV_SUFFIX = "local"
    settings.LOCAL_BEARER_TOKEN = "LOCALBT"
    settings.LOCAL_BEARER_USER_EMAIL = ""
    try:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user_from_auth(
                access_token="LOCALBT",
                db=db,
            )
    finally:
        settings.ENV_SUFFIX = old_env_suffix
        settings.LOCAL_BEARER_TOKEN = old_local_token
        settings.LOCAL_BEARER_USER_EMAIL = old_local_email

    assert exc_info.value.status_code == 401


def test_openapi_requires_auth_when_not_local():
    """M5: /openapi.json must be auth-gated in deployed envs; Swagger UI still 404.

    Unauthenticated callers get 401 (not the schema). The Swagger UI at / stays
    404 in non-local envs. openapi_url is fixed at import time, so we use a
    subprocess to import the app fresh with ENV_SUFFIX=production.
    """
    code = (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "client = TestClient(app)\n"
        "assert client.get('/openapi.json').status_code == 401\n"
        "assert client.get('/').status_code == 404\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "ENV_SUFFIX": "production"},
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
