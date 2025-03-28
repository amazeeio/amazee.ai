import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.database import get_db
from app.db.models import Base
from app.core.config import settings
import os

# Use PostgreSQL for testing in container
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@postgres/postgres_service"
)

# Create test database engine
engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
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

@pytest.fixture(scope="function")
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
    from app.core.security import get_password_hash
    from app.db.models import DBUser

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
    from app.core.security import get_password_hash
    from app.db.models import DBUser

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