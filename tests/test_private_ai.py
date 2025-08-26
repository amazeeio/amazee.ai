import pytest
from unittest.mock import patch, Mock
from app.db.models import DBPrivateAIKey, DBTeam, DBUser, DBProduct, DBTeamProduct
from datetime import datetime, UTC
from app.core.security import get_password_hash
from requests.exceptions import HTTPError
from fastapi import status, HTTPException
from app.core.resource_limits import (
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
)

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
    assert data["id"] != -1

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
        f"/private-ai-keys/{key_id}",
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
    assert data["owner_id"] is not None  # Should be set to admin's ID
    assert data["team_id"] is None

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
    db.refresh(test_key)

    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.id}/spend",
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
    db.refresh(test_key)
    # Try to delete the key as a read_only user
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.id}",
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
        f"/private-ai-keys/{created_key['id']}",
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
        f"/private-ai-keys/{created_key['id']}",
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
        f"/private-ai-keys/{created_key['id']}",
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
        f"/private-ai-keys/{created_key['id']}",
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
    db.refresh(test_key)

    # Try to delete the key as a default user
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.id}",
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
    db.refresh(test_key)
    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.id}/spend",
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
    db.refresh(test_key)

    # View spend information as read-only user
    response = client.get(
        f"/private-ai-keys/{test_key.id}/spend",
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
    db.refresh(test_key)

    # Try to update the budget period as a key_creator
    response = client.put(
        f"/private-ai-keys/{test_key.id}/budget-period",
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

@patch("app.services.litellm.requests.get")
@patch("app.services.litellm.requests.post")
def test_update_budget_duration_as_team_admin(mock_post, mock_get, client, team_admin_token, test_region, mock_litellm_response, db, test_team):
    """Test that a team admin can update the budget duration for a team-owned key"""
    # Mock the LiteLLM API responses
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Mock the key info response
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "info": {
            "spend": 0.0,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z"
        }
    }
    mock_get.return_value.raise_for_status.return_value = None

    # Create a test key owned by the team
    test_key = DBPrivateAIKey(
        database_name="test-db-team",
        name="Test Team Key",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-team",
        litellm_api_url="https://test-litellm.com",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Update the budget duration as team admin
    response = client.put(
        f"/private-ai-keys/{test_key.id}/budget-period",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"budget_duration": "monthly"}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert data["budget_duration"] == "monthly"

    # Verify that the LiteLLM API was called with the correct parameters
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": test_key.litellm_token,
            "budget_duration": "monthly",
            "duration": "365d"
        }
    )

    # Verify that the key info was checked
    mock_get.assert_called_with(
        f"{test_region.litellm_api_url}/key/info",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        params={"key": test_key.litellm_token}
    )

    # Clean up the test key
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.post")
def test_create_llm_token_as_system_admin(mock_post, client, admin_token, test_region, mock_litellm_response):
    """Test that a system admin can create an LLM token for themselves"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    llm_url = test_region.litellm_api_url
    region_name = test_region.name

    # Create LLM token
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test LLM Token"
        }
    )

    # Verify the create response
    assert response.status_code == 200
    data = response.json()
    assert data["litellm_token"] == "test-private-key-123"
    assert data["litellm_api_url"] == llm_url
    assert data["region"] == region_name
    assert data["name"] == "Test LLM Token"

    # Verify the token is visible in list_keys
    list_response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert isinstance(list_data, list)
    assert any(
        key["litellm_token"] == "test-private-key-123" and
        key["litellm_api_url"] == llm_url and
        key["name"] == "Test LLM Token"
        for key in list_data
    )

@patch("app.services.litellm.requests.post")
@patch('app.core.config.settings.ENABLE_LIMITS', True)
def test_create_llm_token_with_expiration(mock_post, client, admin_token, test_region, mock_litellm_response):
    """Test that when ENABLE_LIMITS is true, new LiteLLM tokens are created with a 30-day expiration duration"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # Create LLM token
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test LLM Token with Expiration"
        }
    )

    # Verify the create response
    assert response.status_code == 200
    data = response.json()
    assert data["litellm_token"] == "test-private-key-123"
    assert data["litellm_api_url"] == test_region.litellm_api_url
    assert data["region"] == test_region.name
    assert data["name"] == "Test LLM Token with Expiration"

    # Verify that the LiteLLM API was called with the correct duration
    mock_post.assert_called_once()
    call_args = mock_post.call_args[1]
    assert call_args["json"]["duration"] == "365d"  # Updated default duration
    assert call_args["json"]["budget_duration"] == "30d"  # Verify 1 month
    assert call_args["json"]["max_budget"] == DEFAULT_MAX_SPEND
    assert call_args["json"]["rpm_limit"] == DEFAULT_RPM_PER_KEY

def test_create_vector_db_as_system_admin(client, admin_token, test_region):
    """Test that a system admin can create a vector database for themselves"""
    region_name = test_region.name
    # Create vector database
    response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test Vector DB"
        }
    )

    # Verify the create response
    assert response.status_code == 200
    data = response.json()
    assert data["database_name"] is not None
    assert data["database_host"] is not None
    assert data["database_username"] is not None
    assert data["database_password"] is not None
    assert data["region"] == region_name
    assert data["name"] == "Test Vector DB"

    # Verify the vector DB is visible in list_keys
    list_response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert list_response.status_code == 200
    list_data = list_response.json()
    assert isinstance(list_data, list)
    assert any(
        key["database_name"] == data["database_name"] and
        key["database_host"] == data["database_host"] and
        key["name"] == "Test Vector DB"
        for key in list_data
    )

@patch("app.services.litellm.requests.post")
def test_delete_llm_token_as_system_admin(mock_post, client, admin_token, test_region, mock_litellm_response, db):
    """Test that a system admin can delete an LLM token they own"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    # First create an LLM token
    create_response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test LLM Token to Delete"
        }
    )
    assert create_response.status_code == 200
    created_token = create_response.json()

    # Now delete the token
    delete_response = client.delete(
        f"/private-ai-keys/{created_token['id']}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the token was removed from the database
    deleted_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == created_token["id"]
    ).first()
    assert deleted_key is None

    # Verify the LiteLLM API was called to delete the token
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"keys": [created_token["litellm_token"]]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_vector_db_as_system_admin(mock_post, client, admin_token, test_region, db):
    """Test that a system admin can delete their own vector database"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 404
    mock_post.return_value.json.return_value = {"error": "Key not found"}
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Key not found")

    # First create a vector database
    create_response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test Vector DB to Delete"
        }
    )
    assert create_response.status_code == 200
    created_db = create_response.json()
    assert created_db["name"] == "Test Vector DB to Delete"

    # Now delete the vector database
    delete_response = client.delete(
        f"/private-ai-keys/{created_db['id']}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the vector DB was removed from the database
    deleted_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == created_db["id"]
    ).first()
    assert deleted_key is None

@patch("app.services.litellm.requests.post")
def test_list_private_ai_keys_as_read_only_user_includes_team_keys(mock_post, client, team_admin_token, team_read_only_token, test_region, test_team_read_only, test_team, db):
    """Test that a read-only user can see both their own keys and team-owned keys"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Create a private AI key for the read_only user using team admin token
    user_key_data = {
        "name": "read-only-user-key",
        "region_id": test_region.id,
        "owner_id": test_team_read_only.id  # Create key for the read_only user
    }

    # Create a team-owned key
    team_key_data = {
        "name": "team-owned-key",
        "region_id": test_region.id,
        "team_id": test_team.id  # Create key owned by the team
    }

    # Create both keys as team admin
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json=user_key_data
    )
    assert response.status_code == 200

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json=team_key_data
    )
    assert response.status_code == 200

    # List keys as read_only user
    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {team_read_only_token}"}
    )
    assert response.status_code == 200
    data = response.json()

    # Verify that both the user's own key and team-owned key are returned
    assert len(data) == 2
    assert any(key["name"] == "read-only-user-key" and key["owner_id"] == test_team_read_only.id for key in data)
    assert any(key["name"] == "team-owned-key" and key["team_id"] == test_team.id for key in data)

@patch("app.services.litellm.requests.post")
def test_list_private_ai_keys_as_non_team_user(mock_post, client, admin_token, test_token, test_region, test_user, test_team, test_team_user, db):
    """Test that a user who is not an admin or team member can only see their own keys"""
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Create a key for the test user
    user_key_data = {
        "name": "user-owned-key",
        "region_id": test_region.id,
        "owner_id": test_user.id
    }

    # Create a team-owned key
    team_key_data = {
        "name": "team-owned-key",
        "region_id": test_region.id,
        "team_id": test_team.id
    }

    other_user_key_data = {
        "name": "other-user-key",
        "region_id": test_region.id,
        "owner_id": test_team_user.id
    }

    # Create user's key
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=user_key_data
    )
    assert response.status_code == 200

    # Create team key
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=team_key_data
    )
    assert response.status_code == 200

    # Create other user's key
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=other_user_key_data
    )
    assert response.status_code == 200

    # List keys as non-team user
    response = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    data = response.json()

    print(f"data: {data}")
    # Verify that only the user's own key is returned
    assert len(data) == 1
    assert data[0]["name"] == "user-owned-key"
    assert data[0]["owner_id"] == test_user.id
    assert data[0].get("team_id") is None

@patch("app.services.litellm.requests.get")
def test_get_private_ai_key_success(mock_get, client, admin_token, test_region, db, test_team):
    """Test successfully retrieving a private AI key"""
    region_id = test_region.id
    region_name = test_region.name
    # Create a test key owned by the team
    test_key = DBPrivateAIKey(
        database_name="test-db-get",
        name="Test Key for Get",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-get",
        litellm_api_url="https://test-litellm.com",
        team_id=test_team.id,
        region_id=region_id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Mock the LiteLLM API response for key info
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "info": {
            "key_name": "Test Key for Get",
            "key_alias": "test-key-alias",
            "spend": 0.0,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z",
            "metadata": {
                "team_id": str(test_team.id),
                "region_id": str(test_region.id)
            }
        }
    }
    mock_get.return_value.raise_for_status.return_value = None

    # Get the key as admin
    response = client.get(
        f"/private-ai-keys/{test_key.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == test_key.id
    assert data["name"] == "Test Key for Get"
    assert data["database_name"] == "test-db-get"
    assert data["database_host"] == "test-host"
    assert data["database_username"] == "test-user"
    assert data["database_password"] == "test-pass"
    assert data["litellm_token"] == "test-token-get"
    assert data["litellm_api_url"] == "https://test-litellm.com"
    assert data["team_id"] == test_team.id
    assert data["region"] == region_name

    # Verify the LiteLLM API was called correctly
    mock_get.assert_called_with(
        f"{test_region.litellm_api_url}/key/info",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        params={"key": test_key.litellm_token}
    )

    # Clean up
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.get")
def test_get_private_ai_key_not_found(mock_get, client, admin_token):
    """Test getting a non-existent private AI key"""
    # Try to get a non-existent key
    response = client.get(
        "/private-ai-keys/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the response
    assert response.status_code == 404
    assert "Private AI Key not found" in response.json()["detail"]

@patch("app.services.litellm.requests.get")
def test_get_private_ai_key_unauthorized(mock_get, client, test_token, test_region, db, test_team):
    """Test getting a private AI key without proper authorization"""
    # Create a test key owned by the team
    test_key = DBPrivateAIKey(
        database_name="test-db-unauthorized",
        name="Test Key for Unauthorized",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-unauthorized",
        litellm_api_url="https://test-litellm.com",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Try to get the key as a regular user
    response = client.get(
        f"/private-ai-keys/{test_key.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    # Verify the response
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

    # Clean up
    db.delete(test_key)
    db.commit()

@patch("app.services.litellm.requests.post")
@patch('app.core.config.settings.ENABLE_LIMITS', True)
def test_create_too_many_service_keys(mock_post, client, admin_token, test_region, mock_litellm_response, db, test_team):
    """Test that when ENABLE_LIMITS is true, creating too many service keys fails"""
    # Create a product with a specific service key limit for testing
    key_count = 2
    test_product = DBProduct(
        id="prod_test_service_limit",
        name="Test Product Service Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=key_count,  # Specific limit for testing
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(test_product)
    product_id = test_product.id

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product_id
    )
    db.add(team_product)
    db.commit()

    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    team_id = test_team.id
    # Create service keys up to the limit
    for i in range(key_count):
        response = client.post(
            "/private-ai-keys/token",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "region_id": test_region.id,
                "name": f"Service Key {i+1}",
                "team_id": team_id
            }
        )
        assert response.status_code == 200

    # Try to create one more service key
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Extra Service Key",
            "team_id": team_id
        }
    )
    assert response.status_code == 402
    assert f"Team has reached the maximum service LLM key limit of {key_count} keys" in response.json()["detail"]

@patch("app.services.litellm.requests.post")
@patch('app.core.config.settings.ENABLE_LIMITS', True)
def test_create_too_many_user_keys(mock_post, client, admin_token, test_region, mock_litellm_response, db, test_team_user):
    """Test that when ENABLE_LIMITS is true, creating too many user keys fails"""
    # Get the team from the team user
    team_id = test_team_user.team_id
    key_count = 2

    # Create a product with a specific user key limit for testing
    test_product = DBProduct(
        id="prod_test_user_key_limit",
        name="Test Product User Key Limit",
        user_count=3,
        keys_per_user=key_count,  # Specific limit for testing
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
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=team_id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    user_id = test_team_user.id
    # Create user keys up to the limit
    for i in range(key_count):
        response = client.post(
            "/private-ai-keys/token",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "region_id": test_region.id,
                "name": f"User Key {i+1}",
                "owner_id": user_id
            }
        )
        assert response.status_code == 200

    # Try to create one more user key
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Extra User Key",
            "owner_id": user_id
        }
    )
    assert response.status_code == 402
    assert f"User has reached the maximum LLM key limit of {key_count} keys" in response.json()["detail"]

@patch('app.core.config.settings.ENABLE_LIMITS', True)
def test_create_too_many_vector_dbs(client, admin_token, test_region, db, test_team):
    """Test that when ENABLE_LIMITS is true, creating too many vector DBs fails"""
    # Create a product with a specific vector DB limit for testing
    vector_db_count = 2
    test_product = DBProduct(
        id="prod_test_vector_db_limit",
        name="Test Product Vector DB Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=vector_db_count,  # Specific limit for testing
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()
    db.refresh(test_product)

    # Create vector DBs up to the limit
    team_id = test_team.id
    for i in range(vector_db_count):
        response = client.post(
            "/private-ai-keys/vector-db",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "region_id": test_region.id,
                "name": f"Vector DB {i+1}",
                "team_id": team_id
            }
        )
        assert response.status_code == 200

    # Try to create one more vector DB
    response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Extra Vector DB",
            "team_id": team_id
        }
    )
    assert response.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {vector_db_count} databases" in response.json()["detail"]

@patch("app.services.litellm.requests.post")
@patch('app.core.config.settings.ENABLE_LIMITS', True)
def test_create_too_many_total_keys(mock_post, client, admin_token, test_region, mock_litellm_response, db, test_team, test_team_user):
    """Test that when ENABLE_LIMITS is true, creating too many total keys fails"""
    # Create a product with a specific total key limit for testing
    key_count = 5
    test_product = DBProduct(
        id="prod_test_total_limit",
        name="Test Product Total Limit",
        user_count=5,
        keys_per_user=3,
        total_key_count=key_count,  # Specific limit for testing
        service_key_count=3,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

    team_id = test_team.id
    user_id = test_team_user.id
    region_id = test_region.id

    # Create keys up to the limit (5 keys)
    for i in range(key_count):
        if i % 2 == 0:
            # Create service key
            response = client.post(
                "/private-ai-keys/token",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "region_id": region_id,
                    "name": f"Service Key {i//2 + 1}",
                    "team_id": team_id
                }
            )
        else:
            # Create user key
            response = client.post(
                "/private-ai-keys/token",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={
                    "region_id": region_id,
                    "name": f"User Key {(i//2) + 1}",
                    "owner_id": user_id
                }
            )
        assert response.status_code == 200

    # Try to create one more key
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": region_id,
            "name": "Extra Key",
            "owner_id": user_id
        }
    )
    assert response.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {key_count} keys" in response.json()["detail"]

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_with_only_vector_db(mock_post, client, admin_token, test_region, db):
    """Test deleting a private AI key that only has a vector database (no LLM token)"""
    litellm_api_url = test_region.litellm_api_url
    litellm_api_key = test_region.litellm_api_key
    # Create a test key with only vector DB
    test_key = DBPrivateAIKey(
        database_name="test-db-vector-only",
        name="Test Vector DB Only",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token=None,  # No LLM token
        litellm_api_url=None,  # No LLM API URL
        owner_id=None,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Mock the LiteLLM API response for the None token case
    mock_post.return_value.status_code = 404
    mock_post.return_value.raise_for_status.return_value = None

    # Delete the key
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the key was removed from the database
    deleted_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == test_key.id
    ).first()
    assert deleted_key is None

    # Verify that LiteLLM API was called with None token
    mock_post.assert_called_with(
        f"{litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {litellm_api_key}"},
        json={"keys": [None]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_with_only_llm_token(mock_post, client, admin_token, test_region, db):
    """Test deleting a private AI key that only has an LLM token (no vector DB)"""
    # Create a test key with only LLM token
    api_key = test_region.litellm_api_key
    litellm_api_url = test_region.litellm_api_url
    test_key = DBPrivateAIKey(
        database_name=None,  # No vector DB
        name="Test LLM Token Only",
        database_host=None,
        database_username=None,
        database_password=None,
        litellm_token="test-token-llm-only",
        litellm_api_url="https://test-litellm.com",
        owner_id=None,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    # Delete the key
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 200
    assert delete_response.json()["message"] == "Private AI Key deleted successfully"

    # Verify the key was removed from the database
    deleted_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == test_key.id
    ).first()
    assert deleted_key is None

    # Verify the LiteLLM token was deleted
    mock_post.assert_called_with(
        f"{litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"keys": [test_key.litellm_token]}
    )

@patch("app.services.litellm.requests.post")
def test_delete_private_ai_key_litellm_service_unavailable(mock_post, client, admin_token, test_region, db):
    """Test deleting a private AI key when LiteLLM service returns 503"""
    # Create a test key
    test_key = DBPrivateAIKey(
        database_name="test-db-service-unavailable",
        name="Test Key Service Unavailable",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-service-unavailable",
        litellm_api_url="https://test-litellm.com",
        owner_id=None,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()
    db.refresh(test_key)

    # Mock the LiteLLM API response to return 503
    mock_post.return_value.status_code = 503
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Service Unavailable")

    # Try to delete the key
    delete_response = client.delete(
        f"/private-ai-keys/{test_key.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Verify the delete response
    assert delete_response.status_code == 500
    assert "Failed to delete LiteLLM key" in delete_response.json()["detail"]

    # Verify the key was not removed from the database
    existing_key = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.id == test_key.id
    ).first()
    assert existing_key is not None
    assert existing_key.id == test_key.id

    # Verify the LiteLLM API was called
    mock_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"keys": [test_key.litellm_token]}
    )
    db.commit()

@patch("app.services.litellm.requests.post")
@patch("app.db.postgres.PostgresManager.create_database")
def test_create_private_ai_key_cleanup_on_vector_db_failure(mock_create_db, mock_post, client, test_token, test_region, test_user):
    """
    Given a user creates a private AI key
    When the vector database creation fails after LiteLLM token is created
    Then the LiteLLM token should be cleaned up and an error returned
    """
    # Mock successful LiteLLM token creation
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Mock vector database creation failure
    mock_create_db.side_effect = Exception("Database creation failed")

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    # Verify the response indicates failure
    assert response.status_code == 500
    assert "Failed to create vector database" in response.json()["detail"]

    # Verify LiteLLM token was cleaned up (delete API was called)
    # First call is for token creation, second call is for cleanup
    assert mock_post.call_count == 2

    # Verify the cleanup call
    cleanup_call = mock_post.call_args_list[1]
    assert cleanup_call[0][0] == f"{test_region.litellm_api_url}/key/delete"
    assert cleanup_call[1]["headers"]["Authorization"] == f"Bearer {test_region.litellm_api_key}"
    assert cleanup_call[1]["json"]["keys"] == ["test-private-key-123"]

    # Verify no key was stored in the database
    stored_keys = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"}
    ).json()
    assert len([k for k in stored_keys if k["name"] == "Test AI Key"]) == 0

@patch("app.services.litellm.requests.post")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.db.postgres.PostgresManager.delete_database")
@patch("sqlalchemy.orm.Session.commit")
def test_create_private_ai_key_cleanup_on_db_storage_failure(mock_commit, mock_delete_db, mock_create_db, mock_post, client, test_token, test_region, test_user):
    """
    Given a user creates a private AI key
    When the database storage fails after both LiteLLM token and vector DB are created
    Then both resources should be cleaned up and an error returned
    """
    # Mock successful LiteLLM token creation
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Mock successful vector database creation
    mock_create_db.return_value = {
        "database_name": "test_db_123",
        "database_host": "test-host",
        "database_username": "test_user",
        "database_password": "test_pass"
    }

    # Mock successful vector database deletion
    mock_delete_db.return_value = None

    # Mock database storage failure
    mock_commit.side_effect = Exception("Database storage failed")

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    # Verify the response indicates failure
    assert response.status_code == 500
    assert "Failed to create private AI key" in response.json()["detail"]

    # Verify LiteLLM token was cleaned up
    assert mock_post.call_count == 2
    cleanup_call = mock_post.call_args_list[1]
    assert cleanup_call[0][0] == f"{test_region.litellm_api_url}/key/delete"
    assert cleanup_call[1]["json"]["keys"] == ["test-private-key-123"]

    # Verify vector database was cleaned up
    mock_delete_db.assert_called_once_with("test_db_123")

    # Verify no key was stored in the database
    stored_keys = client.get(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"}
    ).json()
    assert len([k for k in stored_keys if k["name"] == "Test AI Key"]) == 0

@patch("app.services.litellm.requests.post")
@patch("app.db.postgres.PostgresManager.create_database")
def test_create_private_ai_key_cleanup_failure_handling(mock_create_db, mock_post, client, test_token, test_region, test_user):
    """
    Given a user creates a private AI key
    When the cleanup process itself fails
    Then the original error should still be returned to the user
    """
    # Mock successful LiteLLM token creation
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Mock vector database creation failure
    mock_create_db.side_effect = Exception("Database creation failed")

    # Mock cleanup failure - the second call to requests.post will be for cleanup
    # First call succeeds, second call fails
    mock_post.side_effect = [
        Mock(
            status_code=200,
            json=Mock(return_value={"key": "test-private-key-123"}),
            raise_for_status=Mock(return_value=None)
        ),
        Mock(
            status_code=500,
            raise_for_status=Mock(side_effect=HTTPError("Cleanup failed"))
        )
    ]

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    # Verify the response indicates the original failure, not cleanup failure
    assert response.status_code == 500
    assert "Failed to create vector database" in response.json()["detail"]
    assert "Database creation failed" in response.json()["detail"]

    # Verify cleanup was attempted
    assert mock_post.call_count == 2

@patch("app.services.litellm.requests.post")
@patch("app.db.postgres.PostgresManager.create_database")
def test_create_private_ai_key_http_exception_preservation(mock_create_db, mock_post, client, test_token, test_region, test_user):
    """
    Given a user creates a private AI key
    When an HTTPException is raised during creation
    Then the original HTTPException should be preserved and returned
    """
    # Mock successful LiteLLM token creation
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Mock vector database creation failure with HTTPException
    mock_create_db.side_effect = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid database configuration"
    )

    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test AI Key"
        }
    )

    # Verify the original HTTPException is preserved
    assert response.status_code == 500
    assert "Failed to create vector database" in response.json()["detail"]
    assert "Invalid database configuration" in response.json()["detail"]

    # Verify cleanup was attempted
    assert mock_post.call_count == 2
