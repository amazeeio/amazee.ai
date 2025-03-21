import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.db.models import DBRegion, DBPrivateAIKey

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
def mock_litellm_response():
    return {"key": "test-private-key-123"}

def test_create_region(client, test_admin, admin_token):
    """Test creating a new region with private AI key settings"""
    region_data = {
        "name": "new-region",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://new-litellm.com",
        "litellm_api_key": "new-litellm-key"
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == region_data["name"]
    assert data["postgres_host"] == region_data["postgres_host"]
    assert data["litellm_api_url"] == region_data["litellm_api_url"]
    assert "id" in data

def test_create_region_non_admin(client, test_user, test_token):
    """Test that non-admin users cannot create regions"""
    region_data = {
        "name": "new-region",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://new-litellm.com",
        "litellm_api_key": "new-litellm-key"
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {test_token}"},
        json=region_data
    )

    assert response.status_code == 403
    assert "Only administrators can create regions" in response.json()["detail"]

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
        json={"region_id": test_region.id}
    )

    assert response.status_code == 200
    data = response.json()
    assert "database_name" in data
    assert "litellm_token" in data
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
        json={"region_id": 99999}
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

def test_delete_region_with_active_keys(client, admin_token, test_region, db, test_admin):
    """Test that a region with active private AI keys cannot be deleted"""
    # Create a test private AI key in the region
    test_key = DBPrivateAIKey(
        database_name="test-db",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token",
        owner_id=test_admin.id,
        region_id=test_region.id
    )
    db.add(test_key)
    db.commit()

    response = client.delete(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Cannot delete region" in response.json()["detail"]
    assert "database(s) are currently using this region" in response.json()["detail"]