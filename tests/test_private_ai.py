import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.db.models import DBPrivateAIKey, DBTeam, DBUser
import logging
from datetime import datetime, UTC
from app.core.security import get_password_hash

@pytest.fixture
def mock_litellm_response():
    return {"key": "test-private-key-123"}

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key(mock_post, client, test_token, test_region, mock_litellm_response, test_user):
    """Test creating a private AI key in a specific region"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["region"] == test_region.name
    assert data["litellm_token"] == "test-private-key-123"
    assert data["owner_id"] == test_user.id

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_invalid_region(mock_post, client, test_token):
    """Test creating a private AI key with an invalid region ID"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": 99999,
            "name": "Test Invalid Region Key"
        }
    )

    assert response.status_code == 404
    assert "Region not found or inactive" in response.json()["detail"]

def test_list_private_ai_keys(client, test_token, test_region, db, test_user):
    """Test listing private AI keys with region information"""
    # Create a test private AI key
    test_key = DBPrivateAIKey(
        database_name="test-db",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token",
        owner_id=test_user.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["database_name"] == "test-db"
    assert data[0]["region"] == test_region.name
    assert data[0]["owner_id"] == test_user.id

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key(mock_post, client, test_token, test_region, db, test_user):
    """Test deleting a private AI key"""
    # Refresh the test_region to ensure it's attached to the session
    db.refresh(test_region)

    # Store the region values we need for the test
    region_api_url = test_region.litellm_api_url
    region_api_key = test_region.litellm_api_key

    # Create a test private AI key
    test_key = DBPrivateAIKey(
        database_name="test-db-delete",
        name="Test Key to Delete",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-delete",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_user.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # Get the key ID for later verification
    key_id = test_key.id

    # Mock the LiteLLM API delete response
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    # Delete the private AI key
    response = client.delete(
        f"/private-ai-keys/{test_key.database_name}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    # Verify the response
    assert response.status_code == 200
    assert response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the LiteLLM token was deleted
    mock_post.assert_called_once_with(
        f"{region_api_url}/key/delete",
        headers={"Authorization": f"Bearer {region_api_key}"},
        json={"keys": [test_key.litellm_token]}
    )

    # Verify the key was removed from the database
    deleted_key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == key_id).first()
    assert deleted_key is None

@patch("app.services.litellm.requests.post")
def test_list_private_ai_keys_as_team_admin(mock_post, client, team_admin_token, test_team_user, test_region, db):
    """Test that a team admin can list all AI keys associated with users in their team"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # First, get a token for the team user
    response = client.post(
        "/auth/login",
        data={"username": test_team_user.email, "password": "password123"}
    )
    team_user_token = response.json()["access_token"]

    # Create a private AI key as the team user
    key_data = {
        "name": "team-user-key",
        "region_id": test_region.id
    }

    # Create key as team user
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_user_token}"},
        json=key_data
    )
    assert response.status_code == 200

    # List all keys as team admin
    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()

    # Verify that the team user's key is in the response
    assert len(data) > 0
    assert any(key["name"] == "team-user-key" for key in data)
    assert any(key["owner_id"] == test_team_user.id for key in data)

@patch("app.services.litellm.requests.post")
def test_create_team_private_ai_key(mock_post, client, test_team, team_admin_token, test_region, mock_litellm_response):
    """Test creating a private AI key owned by a team"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Team AI Key",
            "team_id": test_team.id
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["region"] == test_region.name
    assert data["litellm_token"] == "test-private-key-123"
    assert data["team_id"] == test_team.id
    assert data["owner_id"] is None

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_without_owner_or_team(mock_post, client, admin_token, test_region):
    """Test that an admin user can create a private AI key without owner_id or team_id, defaulting to themselves as owner"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["region"] == test_region.name
    assert data["litellm_token"] == "test-private-key-123"
    # Verify that the LiteLLM API was called
    mock_post.assert_called_once()

@patch("app.services.litellm.requests.post")
def test_create_team_private_ai_key_as_key_creator(mock_post, client, team_key_creator_token, test_team_id, test_region, mock_litellm_response):
    """Test that a team member with key_creator role cannot create a team key"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a team key as a team member with key_creator role
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_key_creator_token}"},
        json={
            "region_id": test_region.id,
            "name": "Team AI Key",
            "team_id": test_team_id
        }
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_with_both_owner_and_team(mock_post, client, admin_token, test_team, test_team_user, test_region, mock_litellm_response):
    """Test that an admin cannot create a key with both owner_id and team_id set"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a key with both owner_id and team_id set
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Invalid Key",
            "owner_id": test_team_user.id,
            "team_id": test_team.id
        }
    )

    assert response.status_code == 400
    assert "Either owner_id or team_id must be specified, not both" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_as_read_only(mock_post, client, team_read_only_token, test_region, mock_litellm_response):
    """Test that a team member with read_only role cannot create a private AI key"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a private AI key as a read_only team member
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_read_only_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_for_other_team(mock_post, client, team_admin_token, test_region, mock_litellm_response, db):
    """Test that a team admin cannot create a private AI key for another team"""
    # Create a second team
    other_team = DBTeam(
        name="Other Team",
        admin_email="otherteam@example.com",
        phone="0987654321",
        billing_address="456 Other St, Other City, 54321",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a private AI key for the other team
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Other Team Key",
            "team_id": other_team.id
        }
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_for_user_in_other_team(mock_post, client, team_admin_token, test_region, mock_litellm_response, db):
    """Test that a team admin cannot create a private AI key for a user in another team"""
    # Create a second team
    other_team = DBTeam(
        name="Other Team",
        admin_email="otherteam@example.com",
        phone="0987654321",
        billing_address="456 Other St, Other City, 54321",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    # Create a user in the other team
    other_team_user = DBUser(
        email="otherteamuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="user",
        team_id=other_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(other_team_user)
    db.commit()
    db.refresh(other_team_user)

    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a private AI key for the user in the other team
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Other Team User Key",
            "owner_id": other_team_user.id
        }
    )

    assert response.status_code == 404
    assert "Owner user not found" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_create_private_ai_key_for_nonexistent_team(mock_post, client, admin_token, test_region, mock_litellm_response):
    """Test that a system admin cannot create a private AI key for a non-existent team"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Try to create a private AI key for a non-existent team
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Non-existent Team Key",
            "team_id": 99999  # Use a non-existent team ID
        }
    )

    assert response.status_code == 404
    assert "Team not found" in response.json()["detail"]
    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

@patch("app.services.litellm.requests.post")
def test_list_private_ai_keys_as_team_admin_includes_team_and_user_keys(mock_post, client, team_admin_token, test_team_user, test_team, test_region, db):
    """Test that a team admin can list both team keys and keys owned by users in their team"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # First, get a token for the team user
    response = client.post(
        "/auth/login",
        data={"username": test_team_user.email, "password": "password123"}
    )
    team_user_token = response.json()["access_token"]

    # Create a private AI key as the team user
    user_key_data = {
        "name": "team-user-key",
        "region_id": test_region.id
    }

    # Create key as team user
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_user_token}"},
        json=user_key_data
    )
    assert response.status_code == 200

    # Create a team-owned key as team admin
    team_key_data = {
        "name": "team-owned-key",
        "region_id": test_region.id,
        "team_id": test_team.id
    }

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json=team_key_data
    )
    assert response.status_code == 200

    # List all keys as team admin
    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()

    # Verify that both the team user's key and team key are in the response
    assert len(data) == 2
    assert any(key["name"] == "team-user-key" and key["owner_id"] == test_team_user.id for key in data)
    assert any(key["name"] == "team-owned-key" and key["team_id"] == test_team.id for key in data)

@patch("app.services.litellm.requests.post")
def test_list_private_ai_keys_as_read_only_user(mock_post, client, team_admin_token, team_read_only_token, test_region, test_team_read_only, db):
    """Test that a user with read_only access can see their own AI keys"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Create a private AI key for the read_only user using team admin token
    key_data = {
        "name": "read-only-user-key",
        "region_id": test_region.id,
        "owner_id": test_team_read_only.id  # Create key for the read_only user
    }

    # Create key as team admin
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json=key_data
    )
    assert response.status_code == 200

    # List keys as read_only user
    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )
    assert response.status_code == 200
    data = response.json()

    # Verify that only the user's own key is returned
    assert len(data) == 1
    assert data[0]["name"] == "read-only-user-key"
    assert data[0]["owner_id"] == test_team_read_only.id

@patch("app.services.litellm.requests.get")
def test_view_spend_as_read_only_user(mock_get, client, team_read_only_token, test_region, mock_litellm_response, db, test_team_read_only):
    """Test that a read-only user can view spend information for their own key"""
    # Mock the LiteLLM API response
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
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
    mock_get.return_value.raise_for_status.return_value = None

    # Create a test key owned by the read-only user
    test_key = DBPrivateAIKey(
        database_name="test-db-read-only-spend",
        name="Test Key for Spend",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-spend",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_team_read_only.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.database_name}/spend",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert data["spend"] == 10.5
    assert data["expires"] == "2024-12-31T23:59:59Z"
    assert data["created_at"] == "2024-01-01T00:00:00Z"
    assert data["updated_at"] == "2024-01-02T00:00:00Z"
    assert data["max_budget"] == 100.0
    assert data["budget_duration"] == "monthly"
    assert data["budget_reset_at"] == "2024-02-01T00:00:00Z"

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_as_read_only_user(mock_post, client, team_read_only_token, test_region, mock_litellm_response, db):
    """Test that a user with read_only role cannot delete a key"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a test key
    test_key = DBPrivateAIKey(
        database_name="test-db-read-only",
        name="Test Key for Read Only User",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-read-only",
        litellm_api_url="https://test-litellm.com",
        owner_id=1,  # Any owner ID since we're testing read_only access
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # Try to delete the key as a read_only user
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.database_name}",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 403
    assert "Not authorized to perform this action" in delete_response.json()["detail"]

    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.post")
def test_delete_team_private_ai_key(mock_post, client, team_admin_token, test_team, test_region, mock_litellm_response):
    """Test that a team admin can delete a team-owned private AI key"""
    # Mock the LiteLLM API response for both create and delete operations
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # First create a team-owned key
    create_response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Team Key to Delete",
            "team_id": test_team.id
        }
    )
    assert create_response.status_code == 200
    created_key = create_response.json()
    assert created_key["team_id"] == test_team.id
    assert created_key["owner_id"] is None

    # Now delete the team-owned key
    delete_response = client.delete(
        f"/private-ai-keys/{created_key['database_name']}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the LiteLLM token was deleted
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"keys": [created_key["litellm_token"]]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_as_system_admin(mock_post, client, admin_token, test_team_user, test_region, mock_litellm_response):
    """Test that a system admin can delete any private AI key"""
    # Mock the LiteLLM API response for both create and delete operations
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a private AI key for the team user using admin token
    test_team_user_id = test_team_user.id
    create_response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Key to Delete",
            "owner_id": test_team_user_id
        }
    )
    assert create_response.status_code == 200
    created_key = create_response.json()
    assert created_key["owner_id"] == test_team_user_id

    # Now delete the key as system admin
    delete_response = client.delete(
        f"/private-ai-keys/{created_key['database_name']}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the LiteLLM token was deleted
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"keys": [created_key["litellm_token"]]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_from_other_team(mock_post, client, team_admin_token, admin_token, test_region, mock_litellm_response, db):
    """Test that a team admin cannot delete a key from another team"""
    # Create a second team
    other_team = DBTeam(
        name="Other Team",
        admin_email="otherteam@example.com",
        phone="0987654321",
        billing_address="456 Other St, Other City, 54321",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    # Mock the LiteLLM API response for create operation
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a team key for the other team using admin token
    other_team_id = other_team.id
    create_response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Other Team Key",
            "team_id": other_team_id
        }
    )
    assert create_response.status_code == 200
    created_key = create_response.json()
    assert created_key["team_id"] == other_team_id
    assert created_key["owner_id"] is None

    # Try to delete the key as team admin
    delete_response = client.delete(
        f"/private-ai-keys/{created_key['database_name']}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 404
    assert "Private AI Key not found" in delete_response.json()["detail"]

@patch("app.services.litellm.requests.post")
def test_delete_team_member_key_as_team_admin(mock_post, client, team_admin_token, admin_token, test_team_user, test_region, mock_litellm_response):
    """Test that a team admin can delete a key belonging to a user in their team"""
    # Mock the LiteLLM API response for both create and delete operations
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a private AI key for the team user using admin token
    test_team_user_id = test_team_user.id
    create_response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Team Member Key",
            "owner_id": test_team_user_id
        }
    )
    assert create_response.status_code == 200
    created_key = create_response.json()
    assert created_key["owner_id"] == test_team_user_id

    # Delete the key as team admin
    delete_response = client.delete(
        f"/private-ai-keys/{created_key['database_name']}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the LiteLLM token was deleted
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"keys": [created_key["litellm_token"]]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_as_default_user_not_owner(mock_post, client, test_token, test_region, mock_litellm_response, db, test_admin):
    """Test that a default user cannot delete a key they don't own"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a test key owned by the admin user
    test_key = DBPrivateAIKey(
        database_name="test-db-not-owned",
        name="Test Key Not Owned",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-not-owned",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_admin.id,  # Use the admin's ID as the owner
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # Try to delete the key as a default user
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.database_name}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 404
    assert "Private AI Key not found" in delete_response.json()["detail"]

    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.get")
def test_view_spend_with_extra_fields(mock_get, client, team_read_only_token, test_region, mock_litellm_response, db, test_team_read_only):
    """Test that the spend endpoint correctly handles extra fields in the LiteLLM response"""
    # Mock the LiteLLM API response with extra fields
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "info": {
            "spend": 10.5,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z",
            "extra_field1": "value1",  # Extra field not in our model
            "extra_field2": 123,       # Extra field not in our model
            "extra_field3": {          # Extra field not in our model
                "nested": "value"
            }
        }
    }
    mock_get.return_value.raise_for_status.return_value = None

    # Create a test key owned by the read-only user
    test_key = DBPrivateAIKey(
        database_name="test-db-extra-fields",
        name="Test Key with Extra Fields",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-extra-fields",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_team_read_only.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.database_name}/spend",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()

    # Verify only the expected fields are present
    expected_fields = {
        "spend", "expires", "created_at", "updated_at",
        "max_budget", "budget_duration", "budget_reset_at"
    }
    assert set(data.keys()) == expected_fields

    # Verify the values match
    assert data["spend"] == 10.5
    assert data["expires"] == "2024-12-31T23:59:59Z"
    assert data["created_at"] == "2024-01-01T00:00:00Z"
    assert data["updated_at"] == "2024-01-02T00:00:00Z"
    assert data["max_budget"] == 100.0
    assert data["budget_duration"] == "monthly"
    assert data["budget_reset_at"] == "2024-02-01T00:00:00Z"

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.get")
def test_view_spend_with_missing_fields(mock_get, client, team_read_only_token, test_region, mock_litellm_response, db, test_team_read_only):
    """Test that the spend endpoint handles missing spend and budget fields correctly"""
    # Mock the LiteLLM API response with missing fields
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "info": {
            # Missing spend field
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            # Missing max_budget field
            # Missing budget_duration field
            # Missing budget_reset_at field
        }
    }
    mock_get.return_value.raise_for_status.return_value = None

    # Create a test key owned by the read-only user
    test_key = DBPrivateAIKey(
        database_name="test-db-missing-fields",
        name="Test Key with Missing Fields",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-missing-fields",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_team_read_only.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.database_name}/spend",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()

    # Verify all required fields are present
    expected_fields = {
        "spend", "expires", "created_at", "updated_at",
        "max_budget", "budget_duration", "budget_reset_at"
    }
    assert set(data.keys()) == expected_fields

    # Verify the values match
    assert data["spend"] == 0.0  # Default value for missing spend
    assert data["expires"] == "2024-12-31T23:59:59Z"
    assert data["created_at"] == "2024-01-01T00:00:00Z"
    assert data["updated_at"] == "2024-01-02T00:00:00Z"
    assert data["max_budget"] is None  # No default value for missing max_budget
    assert data["budget_duration"] is None  # No default value for missing budget_duration
    assert data["budget_reset_at"] is None  # No default value for missing budget_reset_at

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.post")
def test_update_budget_period_as_key_creator(mock_post, client, team_key_creator_token, test_region, mock_litellm_response, db, test_team_key_creator):
    """Test that a key_creator cannot update the budget period for a key they own"""
    # Mock the LiteLLM API response (though it shouldn't be called)
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create a test key owned by the key_creator user
    test_key = DBPrivateAIKey(
        database_name="test-db-key-creator",
        name="Test Key for Key Creator",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-key-creator",
        litellm_api_url="https://test-litellm.com",
        owner_id=test_team_key_creator.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    # Try to update the budget period as a key_creator
    response = client.put(
        f"/private-ai-keys/{test_key.database_name}/budget-period",
        headers={"Authorization": f"Bearer {team_key_creator_token}"},
        json={"budget_duration": "monthly"}
    )

    # Verify the response
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

    # Verify that the LiteLLM API was not called
    mock_post.assert_not_called()

    # Clean up the test key
    db.delete(test_key)
    db.commit()