import os
# Set environment variables BEFORE any app imports
os.environ["AMAZEEAI_JWT_SECRET"] = "test-secret-key-for-tests"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.db.database import get_db
from app.db.models import Base, DBRegion, DBUser, DBTeam, DBProduct
from app.core.security import get_password_hash
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, MagicMock, Mock, AsyncMock

# Get database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@amazee-test-postgres/postgres_service"
)

# Create test database engine
engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def db():
    # Create the test database and tables
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Create a new session for the test
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        # Clean up after test
        Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def test_user(db):
    # Check if user already exists
    existing_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if existing_user:
        return existing_user

    user = DBUser(
        email="test@example.com",
        hashed_password=get_password_hash("testpassword"),
        is_active=True,
        is_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def test_admin(db):
    admin = DBUser(
        email="admin@example.com",
        hashed_password=get_password_hash("adminpassword"),
        is_active=True,
        is_admin=True
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin

@pytest.fixture
def test_token(client, test_user):
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    return response.json()["access_token"]

@pytest.fixture
def admin_token(client, test_admin):
    response = client.post(
        "/auth/login",
        data={"username": test_admin.email, "password": "adminpassword"}
    )
    return response.json()["access_token"]

@pytest.fixture
def test_team(db):
    team = DBTeam(
        name="Test Team",
        admin_email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team

@pytest.fixture
def test_product(db):
    product = DBProduct(
        id="prod_test123",
        name="Test Product",
        user_count=5,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return product

@pytest.fixture
def test_team_id(test_team):
    return test_team.id

@pytest.fixture
def test_team_user(db, test_team):
    user = DBUser(
        email="teamuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="key_creator",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def test_team_admin(db, test_team):
    user = DBUser(
        email="teamadmin@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="admin",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.merge(test_team)
    db.refresh(test_team)
    return user

@pytest.fixture
def team_admin_token(client, test_team_admin):
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    return response.json()["access_token"]

@pytest.fixture
def test_team_key_creator(db, test_team):
    user = DBUser(
        email="teamkeycreator@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="key_creator",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.merge(test_team)
    db.refresh(test_team)
    return user

@pytest.fixture
def team_key_creator_token(client, test_team_key_creator):
    response = client.post(
        "/auth/login",
        data={"username": test_team_key_creator.email, "password": "password123"}
    )
    return response.json()["access_token"]

@pytest.fixture
def test_region(db):
    region = DBRegion(
        name="test-region",
        label="Test Region",
        description="A test region for automated testing",
        postgres_host="amazee-test-postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://test-litellm.com",
        litellm_api_key="test-litellm-key",
        is_active=True
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region

@pytest.fixture
def test_team_read_only(db, test_team):
    user = DBUser(
        email="teamreadonly@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="read_only",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@pytest.fixture
def team_read_only_token(client, test_team_read_only):
    response = client.post(
        "/auth/login",
        data={"username": test_team_read_only.email, "password": "password123"}
    )
    return response.json()["access_token"]

@pytest.fixture
def mock_sts_client():
    """Fixture to mock STS client responses."""
    with patch('boto3.client') as mock_client:
        # Mock get_caller_identity response
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}

        # Mock assume_role response
        mock_sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'test-access-key',
                'SecretAccessKey': 'test-secret-key',
                'SessionToken': 'test-session-token',
                'Expiration': datetime.now(UTC) + timedelta(hours=1)
            }
        }

        mock_client.return_value = mock_sts
        yield mock_sts

@pytest.fixture
def mock_httpx_post_client():
    """Mock httpx.AsyncClient for POST operations (create/delete/update)"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"key": "test-private-key-123"}
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    return mock_client

@pytest.fixture
def mock_httpx_get_client():
    """Mock httpx.AsyncClient for GET operations (key info)"""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "info": {
            "spend": 10.5,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z"
        }
    }
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    return mock_client

@pytest.fixture
def mock_httpx_combined_client():
    """Mock httpx.AsyncClient for operations that use both POST and GET"""
    # POST response (for create/update/delete operations)
    mock_post_response = Mock()
    mock_post_response.status_code = 200
    mock_post_response.json.return_value = {"key": "test-private-key-123"}
    mock_post_response.raise_for_status.return_value = None

    # GET response (for key info operations)
    mock_get_response = Mock()
    mock_get_response.status_code = 200
    mock_get_response.json.return_value = {
        "info": {
            "spend": 10.5,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z"
        }
    }
    mock_get_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_post_response
    mock_client.get.return_value = mock_get_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    return mock_client


# Helper function for soft-deleting teams in tests
def soft_delete_team_for_test(db, team: DBTeam, deleted_at: datetime = None):
    """
    Helper function to properly soft-delete a team in tests.

    This mimics the production soft-delete behavior:
    - Sets team.deleted_at timestamp
    - Deactivates all users in the team

    Note: Key expiration in LiteLLM should be mocked in tests.

    Args:
        db: Database session
        team: The team to soft delete
        deleted_at: Optional timestamp (defaults to now)
    """
    if deleted_at is None:
        deleted_at = datetime.now(UTC)

    # Set deleted_at timestamp and deactivate team
    team.deleted_at = deleted_at
    team.is_active = False

    # Deactivate all users in the team
    db.query(DBUser).filter(DBUser.team_id == team.id).update(
        {"is_active": False},
        synchronize_session=False
    )

    db.commit()
