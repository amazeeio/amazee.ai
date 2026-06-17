"""
Tests for POST /internal/provision-key.

This endpoint is called exclusively by moad (via AMAZEEAI_ADMIN_API_TOKEN)
to create a LiteLLM token + vector DB as part of the external key provisioning
flow.  Auth is the standard amazee.ai admin API token mechanism.
"""

import os
from unittest.mock import patch, AsyncMock, Mock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.database import get_db
from app.db.models import Base, DBRegion, DBUser, DBTeam
from app.core.security import get_password_hash

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@amazee-test-postgres/postgres_service",
)

engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db):
    user = DBUser(
        email="admin@amazee.ai",
        hashed_password=get_password_hash("adminpass"),
        is_active=True,
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_token(client, admin_user):
    response = client.post(
        "/auth/login",
        data={"username": admin_user.email, "password": "adminpass"},
    )
    return response.json()["access_token"]


@pytest.fixture
def regular_user(db):
    user = DBUser(
        email="user@example.com",
        hashed_password=get_password_hash("userpass"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def regular_token(client, regular_user):
    response = client.post(
        "/auth/login",
        data={"username": regular_user.email, "password": "userpass"},
    )
    return response.json()["access_token"]


@pytest.fixture
def test_team(db):
    team = DBTeam(
        name="Test Team",
        admin_email="team@example.com",
        phone="123",
        billing_address="123 St",
        is_active=True,
        budget_type="periodic",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def test_region(db):
    region = DBRegion(
        name="test-region",
        label="Test Region",
        is_active=True,
        litellm_api_url="http://test-litellm",
        litellm_api_key="test-api-key",
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


# ─── Auth tests ───────────────────────────────────────────────────────────────


def test_internal_provision_key_rejects_no_auth(client, test_region, test_team):
    """Requests without Authorization header must be rejected."""
    response = client.post(
        "/internal/provision-key",
        json={"region_id": test_region.id, "name": "test-key", "team_id": test_team.id},
    )
    assert response.status_code == 401


def test_internal_provision_key_rejects_non_admin(
    client, regular_token, test_region, test_team
):
    """Regular users must not be able to call the internal endpoint."""
    response = client.post(
        "/internal/provision-key",
        headers={"Authorization": f"Bearer {regular_token}"},
        json={"region_id": test_region.id, "name": "test-key", "team_id": test_team.id},
    )
    assert response.status_code in (401, 403)


# ─── Happy path ───────────────────────────────────────────────────────────────


@patch("app.db.postgres.PostgresManager.create_database")
@patch("httpx.AsyncClient")
def test_internal_provision_key_success(
    mock_client_class, mock_create_db, client, admin_token, test_region, test_team
):
    """Admin (moad's AMAZEEAI_ADMIN_API_TOKEN) can create a key via the internal endpoint."""
    mock_create_db.return_value = {
        "database_name": "db_test",
        "database_host": "pghost",
        "database_username": "dbuser",
        "database_password": "dbpass",
    }

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"key": "sk-internal-test-key"}
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    response = client.post(
        "/internal/provision-key",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Drupal CMS - 2026-06-17",
            "team_id": test_team.id,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Drupal CMS - 2026-06-17"
    assert data["team_id"] == test_team.id
    assert "litellm_token" in data
    assert "database_host" in data


def test_internal_provision_key_invalid_region(client, admin_token, test_team):
    """Returns 404 when the region does not exist."""
    response = client.post(
        "/internal/provision-key",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"region_id": 99999, "name": "test-key", "team_id": test_team.id},
    )
    assert response.status_code == 404
