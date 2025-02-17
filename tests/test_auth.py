from fastapi.testclient import TestClient
import pytest
from app.core.security import get_password_hash

def test_login_success(client, test_user):
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_wrong_password(client, test_user):
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

def test_login_nonexistent_user(client):
    response = client.post(
        "/auth/login",
        data={"username": "nonexistent@example.com", "password": "testpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

def test_get_current_user(client, test_token):
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == "test@example.com"
    assert user_data["is_active"] is True
    assert user_data["is_admin"] is False

def test_get_current_user_invalid_token(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid_token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"