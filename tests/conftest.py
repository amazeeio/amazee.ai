import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.db.database import get_db
from app.db.models import Base, DBRegion, DBUser, DBTeam, DBProduct
import os
from app.core.security import get_password_hash
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, MagicMock

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
        try:
            yield db
        finally:
            db.close()

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