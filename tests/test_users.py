import pytest
from fastapi.testclient import TestClient
from app.db.models import DBUser, DBTeam
from datetime import datetime, UTC
from unittest.mock import patch

def test_create_user(client, test_admin, admin_token):
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newuser@example.com",
            "password": "newpassword"
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

def test_delete_team_member(client, admin_token, test_team_user):
    """
    Test deleting a user who is a member of a team and has no AI keys.

    GIVEN: User A is a member of Team 1 and has no AI keys
    WHEN: User A is deleted
    THEN: A 200 - Success is returned, User A is deleted
    """
    response = client.delete(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"

    # Verify user is actually deleted
    response = client.get(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404

@patch("app.services.litellm.requests.post")
def test_delete_team_member_with_ai_keys(mock_post, client, admin_token, test_team_user, test_region, db):
    """
    Test deleting a user who is a member of a team and has associated AI keys.

    GIVEN: User A is a member of Team 1 AND has associated AI Keys
    WHEN: User A is deleted
    THEN: A 400 - Invalid Request is returned
    """
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Create an AI key for the team user
    from app.db.models import DBPrivateAIKey
    ai_key = DBPrivateAIKey(
        database_name=f"test_db_{test_team_user.id}",
        name="Test AI Key",
        database_host="localhost",
        database_username="test_user",
        database_password="test_password",
        litellm_token="test-private-key-123",
        litellm_api_url="http://localhost:8000",
        owner_id=test_team_user.id,
        region_id=test_region.id,
        team_id=test_team_user.team_id
    )
    db.add(ai_key)
    db.commit()
    db.refresh(ai_key)

    # Try to delete the user
    response = client.delete(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400
    assert "Cannot delete user with associated AI keys" in response.json()["detail"]

    # Verify user is not deleted
    response = client.get(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200

def test_create_user_by_team_admin(client, team_admin_token, test_team, db):
    """Test that a team admin can create a user in their own team"""
    # Get the team ID directly from the database to avoid detached instance issues
    team_id = db.query(DBTeam).filter(DBTeam.email == "testteam@example.com").first().id

    # Create a new user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": team_id
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newteamuser@example.com"
    assert user_data["is_admin"] is False
    assert user_data["team_id"] == team_id
    assert "id" in user_data

    # Verify the user is actually in the team in the database
    db_user = db.query(DBUser).filter(DBUser.id == user_data["id"]).first()
    assert db_user.team_id == team_id

    # Verify the user appears in the team's user list
    team_response = client.get(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert team_response.status_code == 200
    team_data = team_response.json()
    assert any(u["id"] == user_data["id"] for u in team_data["users"])

def test_create_user_in_other_team_by_team_admin(client, team_admin_token, db):
    """Test that a team admin cannot create a user in another team"""
    # Create a second team
    team2 = DBTeam(
        name="Team 2",
        email="team2@example.com",
        phone="0987654321",
        billing_address="456 Team 2 St, City 2, 54321",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team2)
    db.commit()
    db.refresh(team2)

    # Try to create a user in the second team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": team2.id
        }
    )
    assert response.status_code == 403
    assert "Team admins can only create users in their own team" in response.json()["detail"]
