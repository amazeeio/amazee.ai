import pytest
from fastapi.testclient import TestClient

def test_create_user(client, test_admin, admin_token):
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newuser@example.com",
            "password": "newpassword",
            "is_admin": False
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newuser@example.com"
    assert user_data["is_admin"] is False
    assert "id" in user_data

def test_create_user_duplicate_email(client, test_user, admin_token, db):
    # Refresh test_user to ensure it's attached to the session
    test_user = db.merge(test_user)
    db.refresh(test_user)

    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": test_user.email,
            "password": "newpassword",
            "is_admin": False
        }
    )
    assert response.status_code == 400
    assert "Email already registered" in response.json()["detail"]

def test_get_users(client, admin_token, test_user):
    response = client.get(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    users = response.json()
    assert isinstance(users, list)
    assert len(users) >= 1
    assert any(user["email"] == test_user.email for user in users)

def test_get_user_by_id(client, admin_token, test_user):
    response = client.get(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == test_user.email
    assert user_data["id"] == test_user.id

def test_get_nonexistent_user(client, admin_token):
    response = client.get(
        "/users/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"

def test_update_user(client, admin_token, test_user):
    response = client.put(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "updated@example.com",
            "is_admin": False,
            "is_active": True
        }
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == "updated@example.com"

def test_delete_user(client, admin_token, test_user):
    response = client.delete(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"

    # Verify user is actually deleted
    response = client.get(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404