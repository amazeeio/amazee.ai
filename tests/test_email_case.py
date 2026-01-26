from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from app.db.models import DBUser
from app.core.config import settings

def test_create_user_uppercase_email_is_stored_lowercase(client: TestClient, admin_token: str, db: Session):
    """
    Given an admin user
    When creating a user with uppercase email in the payload
    Then the user should be stored with lowercase email
    """
    email = "MixedCase@Example.com"
    lowercase_email = email.lower()

    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": email,
            "password": "password123"
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == lowercase_email

    # Verify in DB
    db_user = db.query(DBUser).filter(DBUser.email == lowercase_email).first()
    assert db_user is not None
    assert db_user.email == lowercase_email

def test_login_case_insensitive(client: TestClient, test_user: DBUser):
    """
    Given a user with lowercase email
    When logging in with mixed case email
    Then login should be successful
    """
    # test_user has "test@example.com"
    mixed_case_email = "Test@Example.com"

    response = client.post(
        "/auth/login",
        data={"username": mixed_case_email, "password": "testpassword"}
    )

    assert response.status_code == 200
    assert "access_token" in response.json()

def test_register_case_insensitive_duplicate_check(client: TestClient, test_user: DBUser):
    """
    Given an existing user
    When registering a new user with same email but different casing
    Then registration should fail
    """
    email = test_user.email # "test@example.com"
    mixed_case_email = "Test@Example.com"

    response = client.post(
        "/auth/register",
        json={
            "email": mixed_case_email,
            "password": "newpassword123"
        }
    )

    assert response.status_code == 400
    assert "Email already registered" in response.json()["detail"]

def test_update_email_to_existing_case_insensitive(client: TestClient, test_user: DBUser, admin_token: str, db: Session):
    """
    Given two users
    When updating first user's email to second user's email (mixed case)
    Then update should fail
    """
    # Create another user
    other_email = "other@example.com"
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": other_email,
            "password": "password123"
        }
    )
    assert response.status_code == 201

    # Login as test_user
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    token = response.json()["access_token"]

    # Try to update email to match other_user's email but uppercased
    response = client.put(
        "/auth/me/update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "testpassword",
            "email": "Other@Example.com"
        }
    )

    assert response.status_code == 400
    assert "Email already registered" in response.json()["detail"]
