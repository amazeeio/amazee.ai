from fastapi import HTTPException
from app.db.models import DBTeam, DBRegion
from unittest.mock import patch

@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region(mock_validate_db, mock_validate_litellm, client, admin_token):
    """
    Given an admin user and valid region data
    When they create a new region
    Then the region should be created successfully with validation
    """
    # Mock the validation functions
    mock_validate_litellm.return_value = True
    mock_validate_db.return_value = True

    region_data = {
        "name": "new-region",
        "label": "New Region",
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
    assert data["label"] == region_data["label"]
    assert data["postgres_host"] == region_data["postgres_host"]
    assert data["litellm_api_url"] == region_data["litellm_api_url"]
    assert "id" in data

    # Verify validation functions were called
    mock_validate_litellm.assert_called_once_with(
        region_data["litellm_api_url"],
        region_data["litellm_api_key"]
    )
    mock_validate_db.assert_called_once_with(
        region_data["postgres_host"],
        region_data["postgres_port"],
        region_data["postgres_admin_user"],
        region_data["postgres_admin_password"]
    )

@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_duplicate_name(mock_validate_db, mock_validate_litellm, client, admin_token, test_region):
    """
    Given an admin user and an existing region
    When they try to create a region with the same name
    Then the request should be denied with a 400 error
    """
    # Mock the validation functions (should not be called due to duplicate name check)
    mock_validate_litellm.return_value = True
    mock_validate_db.return_value = True

    region_data = {
        "name": test_region.name,  # Use existing region name
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

    assert response.status_code == 400
    assert f"A region with the name '{test_region.name}' already exists" in response.json()["detail"]

    # Verify validation functions were not called due to early exit
    mock_validate_litellm.assert_not_called()
    mock_validate_db.assert_not_called()

@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_litellm_validation_fails(mock_validate_db, mock_validate_litellm, client, admin_token):
    """
    Given an admin user and region data with invalid LiteLLM endpoint
    When they try to create a region
    Then the request should fail with LiteLLM validation error
    """
    # Mock LiteLLM validation to fail with HTTPException
    mock_validate_litellm.side_effect = HTTPException(
        status_code=400,
        detail="LiteLLM endpoint validation failed: Connection timeout"
    )
    mock_validate_db.return_value = True

    region_data = {
        "name": "new-region",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://invalid-litellm.com",
        "litellm_api_key": "invalid-litellm-key"
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data
    )

    assert response.status_code == 400
    assert "LiteLLM endpoint validation failed" in response.json()["detail"]

    # Verify LiteLLM validation was called but database validation was not
    mock_validate_litellm.assert_called_once()
    mock_validate_db.assert_not_called()

@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_database_validation_fails(mock_validate_db, mock_validate_litellm, client, admin_token):
    """
    Given an admin user and region data with invalid database connection
    When they try to create a region
    Then the request should fail with database validation error
    """
    # Mock LiteLLM validation to succeed, database validation to fail
    mock_validate_litellm.return_value = True
    mock_validate_db.side_effect = HTTPException(
        status_code=400,
        detail="Database connection validation failed: Connection refused"
    )

    region_data = {
        "name": "new-region",
        "postgres_host": "invalid-host",
        "postgres_port": 5432,
        "postgres_admin_user": "invalid-admin",
        "postgres_admin_password": "invalid-password",
        "litellm_api_url": "https://valid-litellm.com",
        "litellm_api_key": "valid-litellm-key"
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data
    )

    assert response.status_code == 400
    assert "Database connection validation failed" in response.json()["detail"]

    # Verify both validations were called
    mock_validate_litellm.assert_called_once()
    mock_validate_db.assert_called_once()

def test_create_region_non_admin(client, test_token):
    """Test that non-admin users cannot create regions"""
    region_data = {
        "name": "new-region",
        "label": "New Region",
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
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_get_region(client, admin_token, test_region):
    """
    Given an admin user and an existing region
    When they get the region by ID
    Then they should receive the region details
    """
    response = client.get(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == test_region.id
    assert data["label"] == test_region.label
    assert data["name"] == test_region.name
    assert data["postgres_host"] == test_region.postgres_host

def test_get_region_non_admin(client, test_token, test_region):
    """
    Given a non-admin user and an existing region
    When they try to get the region by ID
    Then the request should be denied
    """
    response = client.get(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_get_non_existent_region(client, admin_token):
    """
    Given an admin user
    When they try to get a non-existent region
    Then they should receive a 404 error
    """
    response = client.get(
        "/regions/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]

def test_update_region(client, admin_token, test_region):
    """
    Given an admin user and an existing region
    When they update the region
    Then the region should be updated successfully
    """
    update_data = {
        "name": "updated-region-name",
        "label": "Updated Region",
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == update_data["name"]
    assert data["label"] == update_data["label"]
    assert data["postgres_host"] == update_data["postgres_host"]
    assert data["postgres_port"] == update_data["postgres_port"]

def test_update_region_duplicate_name(client, admin_token, test_region, db):
    """
    Given an admin user and two existing regions
    When they try to update one region with the name of another
    Then the request should be denied with a 400 error
    """
    # Create another region
    other_region = DBRegion(
        name="other-region",
        label="Other Region",
        postgres_host="other-host",
        postgres_port=5432,
        postgres_admin_user="other-admin",
        postgres_admin_password="other-password",
        litellm_api_url="https://other-litellm.com",
        litellm_api_key="other-litellm-key",
        is_active=True,
        is_dedicated=False
    )
    db.add(other_region)
    db.commit()
    db.refresh(other_region)

    update_data = {
        "name": other_region.name,  # Use the other region's name
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data
    )

    assert response.status_code == 400
    assert f"A region with the name '{other_region.name}' already exists" in response.json()["detail"]

def test_update_region_non_admin(client, test_token, test_region):
    """
    Given a non-admin user and an existing region
    When they try to update the region
    Then the request should be denied
    """
    update_data = {
        "name": "updated-region-name",
        "label": "Updated Region",
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {test_token}"},
        json=update_data
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_update_non_existent_region(client, admin_token):
    """
    Given an admin user
    When they try to update a non-existent region
    Then they should receive a 404 error
    """
    update_data = {
        "name": "updated-region-name",
        "label": "Updated Region",
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False
    }

    response = client.put(
        "/regions/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]

def test_delete_region_success(client, admin_token, db):
    """
    Given an admin user and a region with no active keys
    When they delete the region
    Then the region should be marked as inactive
    """
    # Create a region for deletion
    region_to_delete = DBRegion(
        name="region-to-delete",
        label="Region to Delete",
        postgres_host="delete-host",
        postgres_port=5432,
        postgres_admin_user="delete-admin",
        postgres_admin_password="delete-password",
        litellm_api_url="https://delete-litellm.com",
        litellm_api_key="delete-litellm-key",
        is_active=True,
        is_dedicated=False
    )
    db.add(region_to_delete)
    db.commit()
    db.refresh(region_to_delete)

    # Store the ID before the API call
    region_id = region_to_delete.id

    response = client.delete(
        f"/regions/{region_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Region deleted successfully"

    # Verify the region is marked as inactive by querying fresh from DB
    deleted_region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    assert not deleted_region.is_active

def test_delete_region_non_admin(client, test_token, test_region):
    """
    Given a non-admin user and an existing region
    When they try to delete the region
    Then the request should be denied
    """
    response = client.delete(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_delete_non_existent_region(client, admin_token):
    """
    Given an admin user
    When they try to delete a non-existent region
    Then they should receive a 404 error
    """
    response = client.delete(
        "/regions/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]

@patch("httpx.AsyncClient")
def test_delete_region_with_active_keys(mock_client_class, client, admin_token, test_region, db, test_admin, mock_httpx_post_client):
    """Test that a region with active private AI keys cannot be deleted"""
    # Use the httpx POST client fixture
    mock_client_class.return_value = mock_httpx_post_client

    # Create a test private AI key in the region via API
    response = client.post(
        "/private-ai-keys/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test Key",
            "owner_id": test_admin.id
        }
    )
    assert response.status_code == 200
    response.json()

    response = client.delete(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Cannot delete region" in response.json()["detail"]
    assert "keys(s) are currently using this region" in response.json()["detail"]

@patch("httpx.AsyncClient")
def test_delete_region_with_active_vector_db(mock_client_class, client, admin_token, test_region, db, test_admin, mock_httpx_post_client):
    """Test that a region with an active vector database cannot be deleted"""
    # Use the httpx POST client fixture
    mock_client_class.return_value = mock_httpx_post_client

    # Create a test vector database in the region via API
    response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": test_region.id,
            "name": "Test Vector DB",
            "owner_id": test_admin.id
        }
    )
    assert response.status_code == 200
    response.json()

    response = client.delete(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Cannot delete region" in response.json()["detail"]
    assert "keys(s) are currently using this region" in response.json()["detail"]

def test_list_admin_regions(client, admin_token, db, test_region):
    """
    Given an admin user and regions in the system
    When they list admin regions
    Then they should see all regions regardless of active status
    """
    # Create an inactive region
    inactive_region = DBRegion(
        name="inactive-region",
        label="Inactive Region",
        postgres_host="inactive-host",
        postgres_port=5432,
        postgres_admin_user="inactive-admin",
        postgres_admin_password="inactive-password",
        litellm_api_url="https://inactive-litellm.com",
        litellm_api_key="inactive-litellm-key",
        is_active=False,
        is_dedicated=False
    )
    db.add(inactive_region)
    db.commit()

    response = client.get(
        "/regions/admin",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) >= 2  # At least test_region and inactive_region
    region_names = [r["name"] for r in regions]
    assert test_region.name in region_names
    assert "inactive-region" in region_names

def test_list_admin_regions_non_admin(client, test_token):
    """
    Given a non-admin user
    When they try to list admin regions
    Then the request should be denied
    """
    response = client.get(
        "/regions/admin",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

# Dedicated Regions Tests

def test_list_regions_regular_user_sees_non_dedicated_only(client, test_token, db, test_region):
    """
    Given a regular user and regions with different dedication statuses
    When the user lists regions
    Then they should only see non-dedicated regions
    """
    # Create a dedicated region
    dedicated_region = db.query(DBRegion).filter(DBRegion.name == "dedicated-region").first()
    if not dedicated_region:
        dedicated_region = DBRegion(
            name="dedicated-region",
            label="Dedicated Region",
            postgres_host="dedicated-host",
            postgres_port=5432,
            postgres_admin_user="dedicated-admin",
            postgres_admin_password="dedicated-password",
            litellm_api_url="https://dedicated-litellm.com",
            litellm_api_key="dedicated-litellm-key",
            is_active=True,
            is_dedicated=True
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    response = client.get(
        "/regions/",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 1
    assert regions[0]["name"] == test_region.name
    assert regions[0]["name"] != "dedicated-region"

def test_list_regions_admin_sees_all_regions(client, admin_token, db, test_region):
    """
    Given an admin user and regions with different dedication statuses
    When the admin lists regions
    Then they should see all regions regardless of dedication status
    """
    # Create a dedicated region
    dedicated_region = db.query(DBRegion).filter(DBRegion.name == "dedicated-region").first()
    if not dedicated_region:
        dedicated_region = DBRegion(
            name="dedicated-region",
            label="Dedicated Region",
            postgres_host="dedicated-host",
            postgres_port=5432,
            postgres_admin_user="dedicated-admin",
            postgres_admin_password="dedicated-password",
            litellm_api_url="https://dedicated-litellm.com",
            litellm_api_key="dedicated-litellm-key",
            is_active=True,
            is_dedicated=True
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    response = client.get(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 2
    region_names = [r["name"] for r in regions]
    assert test_region.name in region_names
    assert "dedicated-region" in region_names

def test_list_regions_team_member_sees_team_dedicated_regions(client, team_admin_token, db, test_region, test_team):
    """
    Given a team member and a dedicated region associated with their team
    When the team member lists regions
    Then they should see non-dedicated regions plus their team's dedicated regions
    """
    # Create a dedicated region associated with the team
    dedicated_region = db.query(DBRegion).filter(DBRegion.name == "team-dedicated-region").first()
    if not dedicated_region:
        dedicated_region = DBRegion(
            name="team-dedicated-region",
            label="Team Dedicated Region",
            postgres_host="team-dedicated-host",
            postgres_port=5432,
            postgres_admin_user="team-dedicated-admin",
            postgres_admin_password="team-dedicated-password",
            litellm_api_url="https://team-dedicated-litellm.com",
            litellm_api_key="team-dedicated-litellm-key",
            is_active=True,
            is_dedicated=True
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion
    team_region = DBTeamRegion(
        team_id=test_team.id,
        region_id=dedicated_region.id
    )
    db.add(team_region)
    db.commit()

    response = client.get(
        "/regions/",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 2
    region_names = [r["name"] for r in regions]
    assert test_region.name in region_names
    assert "team-dedicated-region" in region_names

def test_list_regions_team_member_does_not_see_other_team_dedicated_regions(client, team_admin_token, db, test_region, test_team):
    """
    Given a team member and a dedicated region associated with a different team
    When the team member lists regions
    Then they should not see the other team's dedicated regions
    """
    # Create another team
    other_team = DBTeam(
        name="Other Team",
        admin_email="other@example.com",
        is_active=True
    )
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    # Create a dedicated region associated with the other team
    dedicated_region = db.query(DBRegion).filter(DBRegion.name == "other-team-dedicated-region").first()
    if not dedicated_region:
        dedicated_region = DBRegion(
            name="other-team-dedicated-region",
            label="Other Team Dedicated Region",
            postgres_host="other-team-dedicated-host",
            postgres_port=5432,
            postgres_admin_user="other-team-dedicated-admin",
            postgres_admin_password="other-team-dedicated-password",
            litellm_api_url="https://other-team-dedicated-litellm.com",
            litellm_api_key="other-team-dedicated-litellm-key",
            is_active=True,
            is_dedicated=True
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    # Create team-region association for other team
    from app.db.models import DBTeamRegion
    team_region = DBTeamRegion(
        team_id=other_team.id,
        region_id=dedicated_region.id
    )
    db.add(team_region)
    db.commit()

    response = client.get(
        "/regions/",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 1
    assert regions[0]["name"] == test_region.name
    assert "other-team-dedicated-region" not in [r["name"] for r in regions]

@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_dedicated_region(mock_validate_db, mock_validate_litellm, client, admin_token):
    """
    Given an admin user
    When they create a region with is_dedicated=True
    Then the region should be created successfully
    """
    # Mock the validation functions
    mock_validate_litellm.return_value = True
    mock_validate_db.return_value = True

    region_data = {
        "name": "new-dedicated-region",
        "label": "New Dedicated Region",
        "postgres_host": "new-dedicated-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-dedicated-admin",
        "postgres_admin_password": "new-dedicated-password",
        "litellm_api_url": "https://new-dedicated-litellm.com",
        "litellm_api_key": "new-dedicated-litellm-key",
        "is_dedicated": True
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == region_data["name"]
    assert data["label"] == region_data["label"]
    assert data["is_dedicated"]

    # Verify validation functions were called
    mock_validate_litellm.assert_called_once_with(
        region_data["litellm_api_url"],
        region_data["litellm_api_key"]
    )
    mock_validate_db.assert_called_once_with(
        region_data["postgres_host"],
        region_data["postgres_port"],
        region_data["postgres_admin_user"],
        region_data["postgres_admin_password"]
    )

def test_create_dedicated_region_non_admin_fails(client, test_token):
    """
    Given a non-admin user
    When they try to create a dedicated region
    Then the request should be denied
    """
    region_data = {
        "name": "new-dedicated-region",
        "label": "New Dedicated Region",
        "postgres_host": "new-dedicated-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-dedicated-admin",
        "postgres_admin_password": "new-dedicated-password",
        "litellm_api_url": "https://new-dedicated-litellm.com",
        "litellm_api_key": "new-dedicated-litellm-key",
        "is_dedicated": True
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {test_token}"},
        json=region_data
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_associate_team_with_dedicated_region(client, admin_token, db, test_team):
    """
    Given an admin user and a dedicated region
    When they associate a team with the region
    Then the association should be created successfully
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-association",
        label="Dedicated Region for Association",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team associated with region successfully"

def test_associate_team_with_non_dedicated_region_fails(client, admin_token, db, test_team, test_region):
    """
    Given an admin user and a non-dedicated region
    When they try to associate a team with the region
    Then the request should be denied
    """
    response = client.post(
        f"/regions/{test_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Can only associate teams with dedicated regions" in response.json()["detail"]

def test_associate_team_with_region_non_admin_fails(client, test_token, db, test_team):
    """
    Given a non-admin user
    When they try to associate a team with a region
    Then the request should be denied
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-non-admin-test",
        label="Dedicated Region for Non-Admin Test",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_associate_team_with_non_existent_region(client, admin_token, db, test_team):
    """
    Given an admin user and a non-existent region
    When they try to associate a team with the region
    Then they should receive a 404 error
    """
    response = client.post(
        f"/regions/99999/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]

def test_associate_non_existent_team_with_region(client, admin_token, db):
    """
    Given an admin user and a non-existent team
    When they try to associate the team with a region
    Then they should receive a 404 error
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-non-existent-team",
        label="Dedicated Region for Non-Existent Team",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Team not found" in response.json()["detail"]

def test_associate_team_with_already_associated_region(client, admin_token, db, test_team):
    """
    Given an admin user and a team already associated with a region
    When they try to associate the team with the same region again
    Then the request should be denied with a 400 error
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-duplicate-association",
        label="Dedicated Region for Duplicate Association",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create initial association
    from app.db.models import DBTeamRegion
    team_region = DBTeamRegion(
        team_id=test_team.id,
        region_id=dedicated_region.id
    )
    db.add(team_region)
    db.commit()

    # Try to associate again
    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Team is already associated with this region" in response.json()["detail"]

def test_disassociate_team_from_dedicated_region(client, admin_token, db, test_team):
    """
    Given an admin user and a team associated with a dedicated region
    When they disassociate the team from the region
    Then the association should be removed successfully
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-disassociation",
        label="Dedicated Region for Disassociation",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion
    team_region = DBTeamRegion(
        team_id=test_team.id,
        region_id=dedicated_region.id
    )
    db.add(team_region)
    db.commit()

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team disassociated from region successfully"

def test_disassociate_team_from_region_non_admin_fails(client, test_token, db, test_team):
    """
    Given a non-admin user
    When they try to disassociate a team from a region
    Then the request should be denied
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-non-admin-disassociation",
        label="Dedicated Region for Non-Admin Disassociation",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_disassociate_team_from_non_existent_association(client, admin_token, db, test_team):
    """
    Given an admin user and a non-existent team-region association
    When they try to disassociate the team from the region
    Then they should receive a 404 error
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-non-existent-disassociation",
        label="Dedicated Region for Non-Existent Disassociation",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Team-region association not found" in response.json()["detail"]

def test_list_teams_for_dedicated_region(client, admin_token, db, test_team):
    """
    Given an admin user and a dedicated region with associated teams
    When they list teams for the region
    Then they should see all associated teams
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-team-listing",
        label="Dedicated Region for Team Listing",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion
    team_region = DBTeamRegion(
        team_id=test_team.id,
        region_id=dedicated_region.id
    )
    db.add(team_region)
    db.commit()

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 1
    assert teams[0]["id"] == test_team.id
    assert teams[0]["name"] == test_team.name

def test_list_teams_for_dedicated_region_non_admin_fails(client, test_token, db):
    """
    Given a non-admin user
    When they try to list teams for a region
    Then the request should be denied
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-for-non-admin-team-listing",
        label="Dedicated Region for Non-Admin Team Listing",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_list_teams_for_non_dedicated_region_fails(client, admin_token, test_region):
    """
    Given an admin user and a non-dedicated region
    When they try to list teams for the region
    Then the request should be denied
    """
    response = client.get(
        f"/regions/{test_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Can only list teams for dedicated regions" in response.json()["detail"]

def test_list_teams_for_non_existent_region(client, admin_token):
    """
    Given an admin user
    When they try to list teams for a non-existent region
    Then they should receive a 404 error
    """
    response = client.get(
        "/regions/99999/teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]

def test_list_teams_for_dedicated_region_with_no_associations(client, admin_token, db):
    """
    Given an admin user and a dedicated region with no team associations
    When they list teams for the region
    Then they should receive an empty list
    """
    # Create a dedicated region
    dedicated_region = DBRegion(
        name="dedicated-region-with-no-teams",
        label="Dedicated Region with No Teams",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 0
