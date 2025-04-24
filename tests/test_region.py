import pytest
from app.db.models import DBPrivateAIKey

def test_create_region(client, admin_token):
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

def test_create_region_non_admin(client, test_token):
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
