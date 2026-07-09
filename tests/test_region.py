from fastapi import HTTPException
from app.db.models import DBTeam, DBRegion, DBTeamRegion
from unittest.mock import patch, AsyncMock
from app.services.litellm import LiteLLMService


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
        "description": "A new test region with description",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://new-litellm.com",
        "litellm_api_key": "new-litellm-key",
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == region_data["name"]
    assert data["label"] == region_data["label"]
    assert data["description"] == region_data["description"]
    assert data["postgres_host"] == region_data["postgres_host"]
    assert data["litellm_api_url"] == region_data["litellm_api_url"]
    assert "id" in data

    # Verify validation functions were called
    mock_validate_litellm.assert_called_once_with(
        region_data["litellm_api_url"], region_data["litellm_api_key"]
    )
    mock_validate_db.assert_called_once_with(
        region_data["postgres_host"],
        region_data["postgres_port"],
        region_data["postgres_admin_user"],
        region_data["postgres_admin_password"],
    )


@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_duplicate_name(
    mock_validate_db, mock_validate_litellm, client, admin_token, test_region
):
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
        "label": "New Region",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://new-litellm.com",
        "litellm_api_key": "new-litellm-key",
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data,
    )

    assert response.status_code == 400
    assert (
        f"A region with the name '{test_region.name}' already exists"
        in response.json()["detail"]
    )

    # Verify validation functions were not called due to early exit
    mock_validate_litellm.assert_not_called()
    mock_validate_db.assert_not_called()


@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_litellm_validation_fails(
    mock_validate_db, mock_validate_litellm, client, admin_token
):
    """
    Given an admin user and region data with invalid LiteLLM endpoint
    When they try to create a region
    Then the request should fail with LiteLLM validation error
    """
    # Mock LiteLLM validation to fail with HTTPException
    mock_validate_litellm.side_effect = HTTPException(
        status_code=400, detail="LiteLLM endpoint validation failed: Connection timeout"
    )
    mock_validate_db.return_value = True

    region_data = {
        "name": "new-region",
        "label": "New Region",
        "postgres_host": "new-host",
        "postgres_port": 5432,
        "postgres_admin_user": "new-admin",
        "postgres_admin_password": "new-password",
        "litellm_api_url": "https://invalid-litellm.com",
        "litellm_api_key": "invalid-litellm-key",
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data,
    )

    assert response.status_code == 400
    assert "LiteLLM endpoint validation failed" in response.json()["detail"]

    # Verify LiteLLM validation was called but database validation was not
    mock_validate_litellm.assert_called_once()
    mock_validate_db.assert_not_called()


@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_region_database_validation_fails(
    mock_validate_db, mock_validate_litellm, client, admin_token
):
    """
    Given an admin user and region data with invalid database connection
    When they try to create a region
    Then the request should fail with database validation error
    """
    # Mock LiteLLM validation to succeed, database validation to fail
    mock_validate_litellm.return_value = True
    mock_validate_db.side_effect = HTTPException(
        status_code=400,
        detail="Database connection validation failed: Connection refused",
    )

    region_data = {
        "name": "new-region",
        "label": "New Region",
        "postgres_host": "invalid-host",
        "postgres_port": 5432,
        "postgres_admin_user": "invalid-admin",
        "postgres_admin_password": "invalid-password",
        "litellm_api_url": "https://valid-litellm.com",
        "litellm_api_key": "valid-litellm-key",
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data,
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
        "litellm_api_key": "new-litellm-key",
    }

    response = client.post(
        "/regions/", headers={"Authorization": f"Bearer {test_token}"}, json=region_data
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
        f"/regions/{test_region.id}", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == test_region.id
    assert data["label"] == test_region.label
    assert data["description"] == test_region.description
    assert data["name"] == test_region.name
    assert data["postgres_host"] == test_region.postgres_host


def test_get_region_non_admin(client, test_token, test_region):
    """
    Given a non-admin user and an existing region
    When they try to get the region by ID
    Then the request should be denied
    """
    response = client.get(
        f"/regions/{test_region.id}", headers={"Authorization": f"Bearer {test_token}"}
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
        "/regions/99999", headers={"Authorization": f"Bearer {admin_token}"}
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
        "description": "Updated description for test region",
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False,
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == update_data["name"]
    assert data["label"] == update_data["label"]
    assert data["description"] == update_data["description"]
    assert data["postgres_host"] == update_data["postgres_host"]
    assert data["postgres_port"] == update_data["postgres_port"]


def test_update_region_legacy_http_url_grandfathered(
    client, admin_token, test_region, db
):
    """
    Given a legacy region whose stored litellm_api_url is still http://
    When an admin updates unrelated fields without changing the URL
    Then the update succeeds (unchanged legacy values are not revalidated)
    """
    test_region.litellm_api_url = "http://legacy-litellm.internal"
    db.commit()

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": test_region.name,
            "postgres_host": test_region.postgres_host,
            "postgres_port": test_region.postgres_port,
            "postgres_admin_user": test_region.postgres_admin_user,
            "litellm_api_url": "http://legacy-litellm.internal",
            "is_active": False,
            "is_dedicated": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_update_region_rejects_new_http_url(client, admin_token, test_region):
    """
    Given an existing region with an https litellm_api_url
    When an admin tries to change it to an http:// URL
    Then the request is rejected with a 422 error
    """
    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": test_region.name,
            "postgres_host": test_region.postgres_host,
            "postgres_port": test_region.postgres_port,
            "postgres_admin_user": test_region.postgres_admin_user,
            "litellm_api_url": "http://new-litellm.internal",
            "is_active": True,
            "is_dedicated": False,
        },
    )

    assert response.status_code == 422
    assert "https" in response.json()["detail"]


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
        is_dedicated=False,
    )
    db.add(other_region)
    db.commit()
    db.refresh(other_region)

    update_data = {
        "name": other_region.name,  # Use the other region's name
        "label": "Updated Region",
        "postgres_host": "updated-host",
        "postgres_port": 5433,
        "postgres_admin_user": "updated-admin",
        "postgres_admin_password": "updated-password",
        "litellm_api_url": "https://updated-litellm.com",
        "litellm_api_key": "updated-litellm-key",
        "is_active": True,
        "is_dedicated": False,
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data,
    )

    assert response.status_code == 400
    assert (
        f"A region with the name '{other_region.name}' already exists"
        in response.json()["detail"]
    )


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
        "is_dedicated": False,
    }

    response = client.put(
        f"/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {test_token}"},
        json=update_data,
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
        "is_dedicated": False,
    }

    response = client.put(
        "/regions/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=update_data,
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
        is_dedicated=False,
    )
    db.add(region_to_delete)
    db.commit()
    db.refresh(region_to_delete)

    # Store the ID before the API call
    region_id = region_to_delete.id

    response = client.delete(
        f"/regions/{region_id}", headers={"Authorization": f"Bearer {admin_token}"}
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
        f"/regions/{test_region.id}", headers={"Authorization": f"Bearer {test_token}"}
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
        "/regions/99999", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 404
    assert "Region not found" in response.json()["detail"]


@patch("httpx.AsyncClient")
def test_delete_region_with_active_keys(
    mock_client_class,
    client,
    admin_token,
    test_region,
    db,
    test_admin,
    mock_httpx_post_client,
):
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
            "owner_id": test_admin.id,
        },
    )
    assert response.status_code == 200
    response.json()

    response = client.delete(
        f"/regions/{test_region.id}", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 400
    assert "Cannot delete region" in response.json()["detail"]
    assert "keys(s) are currently using this region" in response.json()["detail"]


@patch("httpx.AsyncClient")
def test_delete_region_with_active_vector_db(
    mock_client_class,
    client,
    admin_token,
    test_region,
    db,
    test_admin,
    mock_httpx_post_client,
):
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
            "owner_id": test_admin.id,
        },
    )
    assert response.status_code == 200
    response.json()

    response = client.delete(
        f"/regions/{test_region.id}", headers={"Authorization": f"Bearer {admin_token}"}
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
        is_dedicated=False,
    )
    db.add(inactive_region)
    db.commit()

    response = client.get(
        "/regions/admin", headers={"Authorization": f"Bearer {admin_token}"}
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
        "/regions/admin", headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


# Dedicated Regions Tests


def test_list_regions_regular_user_sees_non_dedicated_only(
    client, test_token, db, test_region
):
    """
    Given a regular user and regions with different dedication statuses
    When the user lists regions
    Then they should only see non-dedicated regions
    """
    # Create a dedicated region
    dedicated_region = (
        db.query(DBRegion).filter(DBRegion.name == "dedicated-region").first()
    )
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
            is_dedicated=True,
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    response = client.get(
        "/regions/", headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 1
    assert regions[0]["name"] == test_region.name
    assert regions[0]["label"] == test_region.label
    assert regions[0]["name"] != "dedicated-region"


def test_list_regions_admin_sees_all_regions(client, admin_token, db, test_region):
    """
    Given an admin user and regions with different dedication statuses
    When the admin lists regions
    Then they should see all regions regardless of dedication status
    """
    # Create a dedicated region
    dedicated_region = (
        db.query(DBRegion).filter(DBRegion.name == "dedicated-region").first()
    )
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
            is_dedicated=True,
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    response = client.get(
        "/regions/", headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 2
    region_names = [r["name"] for r in regions]
    assert test_region.name in region_names
    assert "dedicated-region" in region_names


def test_list_regions_team_member_sees_team_dedicated_regions(
    client, team_admin_token, db, test_region, test_team
):
    """
    Given a team member and a dedicated region associated with their team
    When the team member lists regions
    Then they should see non-dedicated regions plus their team's dedicated regions
    """
    # Create a dedicated region associated with the team
    dedicated_region = (
        db.query(DBRegion).filter(DBRegion.name == "team-dedicated-region").first()
    )
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
            is_dedicated=True,
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion

    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    response = client.get(
        "/regions/", headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 2
    region_names = [r["name"] for r in regions]
    assert test_region.name in region_names
    assert "team-dedicated-region" in region_names


def test_list_regions_team_member_with_only_dedicated_assignment(
    client, team_admin_token, db, test_region, test_team
):
    """
    Given a team where only a dedicated region is assigned
    When a team member lists regions
    Then they should only see that explicit assignment
    """
    from app.db.models import DBTeamRegion

    # Remove the default public association for this team.
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id,
        DBTeamRegion.region_id == test_region.id,
    ).delete()
    db.commit()

    # Create a dedicated region associated with the team
    dedicated_region = (
        db.query(DBRegion)
        .filter(DBRegion.name == "team-hidden-public-dedicated")
        .first()
    )
    if not dedicated_region:
        dedicated_region = DBRegion(
            name="team-hidden-public-dedicated",
            label="Team Hidden Public Dedicated",
            postgres_host="team-hidden-public-host",
            postgres_port=5432,
            postgres_admin_user="team-hidden-public-admin",
            postgres_admin_password="team-hidden-public-password",
            litellm_api_url="https://team-hidden-public-litellm.com",
            litellm_api_key="team-hidden-public-litellm-key",
            is_active=True,
            is_dedicated=True,
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    response = client.get(
        "/regions/", headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 1
    assert regions[0]["name"] == "team-hidden-public-dedicated"
    assert test_region.name not in [r["name"] for r in regions]


def test_list_regions_team_member_does_not_see_other_team_dedicated_regions(
    client, team_admin_token, db, test_region, test_team
):
    """
    Given a team member and a dedicated region associated with a different team
    When the team member lists regions
    Then they should not see the other team's dedicated regions
    """
    # Create another team
    other_team = DBTeam(
        name="Other Team", admin_email="other@example.com", is_active=True
    )
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    # Create a dedicated region associated with the other team
    dedicated_region = (
        db.query(DBRegion)
        .filter(DBRegion.name == "other-team-dedicated-region")
        .first()
    )
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
            is_dedicated=True,
        )
        db.add(dedicated_region)
        db.commit()
        db.refresh(dedicated_region)

    # Create team-region association for other team
    from app.db.models import DBTeamRegion

    team_region = DBTeamRegion(team_id=other_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    response = client.get(
        "/regions/", headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    assert response.status_code == 200
    regions = response.json()
    assert len(regions) == 1
    assert regions[0]["name"] == test_region.name
    assert regions[0]["label"] == test_region.label
    assert "other-team-dedicated-region" not in [r["name"] for r in regions]


@patch("app.api.regions.validate_litellm_endpoint")
@patch("app.api.regions.validate_database_connection")
def test_create_dedicated_region(
    mock_validate_db, mock_validate_litellm, client, admin_token
):
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
        "is_dedicated": True,
    }

    response = client.post(
        "/regions/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=region_data,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == region_data["name"]
    assert data["label"] == region_data["label"]
    assert data["is_dedicated"]

    # Verify validation functions were called
    mock_validate_litellm.assert_called_once_with(
        region_data["litellm_api_url"], region_data["litellm_api_key"]
    )
    mock_validate_db.assert_called_once_with(
        region_data["postgres_host"],
        region_data["postgres_port"],
        region_data["postgres_admin_user"],
        region_data["postgres_admin_password"],
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
        "is_dedicated": True,
    }

    response = client.post(
        "/regions/", headers={"Authorization": f"Bearer {test_token}"}, json=region_data
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


@patch("app.api.regions.LiteLLMService.create_team", new_callable=AsyncMock)
def test_associate_team_with_dedicated_region(
    mock_create_team, client, admin_token, db, test_team
):
    """
    Given an admin user and a dedicated region
    When they associate a team with the region
    Then the association should be created and the team bootstrapped in LiteLLM
    """
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team associated with region successfully"
    mock_create_team.assert_called_once()
    call_kwargs = mock_create_team.call_args.kwargs
    assert call_kwargs["team_id"] == LiteLLMService.format_team_id(
        "dedicated-region-for-association", test_team.id
    )
    # PERIODIC team: max_budget should be DEFAULT_MAX_SPEND, no budget_duration
    assert call_kwargs["max_budget"] > 0
    assert call_kwargs["budget_duration"] is None


@patch("app.api.regions.LiteLLMService.create_team", new_callable=AsyncMock)
def test_associate_pool_team_with_dedicated_region(
    mock_create_team, client, admin_token, db
):
    """
    Given a POOL team and a dedicated region
    When they are associated
    Then LiteLLM team is created with max_budget=0 and a budget_duration
    """
    from app.db.models import DBTeam

    pool_team = DBTeam(
        name="pool-team-for-dedicated",
        admin_email="pool-dedicated@test.com",
        budget_type="pool",
    )
    db.add(pool_team)

    dedicated_region = DBRegion(
        name="dedicated-region-for-pool",
        label="Dedicated for Pool",
        postgres_host="h",
        postgres_port=5432,
        postgres_admin_user="a",
        postgres_admin_password="p",
        litellm_api_url="https://pool-dedicated-litellm.com",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(pool_team)
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{pool_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    call_kwargs = mock_create_team.call_args.kwargs
    assert call_kwargs["max_budget"] == 0.0
    assert call_kwargs["budget_duration"] is not None


@patch(
    "app.api.regions.LiteLLMService.create_team",
    new_callable=AsyncMock,
    side_effect=Exception("LiteLLM unreachable"),
)
def test_associate_team_litellm_failure_rolls_back(
    mock_create_team, client, admin_token, db, test_team
):
    """
    Given a LiteLLM failure during association
    When the endpoint is called
    Then it returns 502 and the DB association is rolled back
    """
    from app.db.models import DBTeamRegion

    dedicated_region = DBRegion(
        name="dedicated-region-litellm-fail",
        label="Dedicated LiteLLM Fail",
        postgres_host="h",
        postgres_port=5432,
        postgres_admin_user="a",
        postgres_admin_password="p",
        litellm_api_url="https://fail-litellm.com",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 502
    assert "Failed to bootstrap team in LiteLLM" in response.json()["detail"]

    # DB association must not exist
    association = (
        db.query(DBTeamRegion)
        .filter(
            DBTeamRegion.team_id == test_team.id,
            DBTeamRegion.region_id == dedicated_region.id,
        )
        .first()
    )
    assert association is None


@patch("app.api.regions.sync_add_user_to_team", new_callable=AsyncMock)
@patch("app.api.regions.LiteLLMService.create_team", new_callable=AsyncMock)
def test_associate_team_with_non_dedicated_region(
    mock_create_team,
    mock_sync_add_user_to_team,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    """
    Given an admin user and a non-dedicated region
    When they associate a team with the region
    Then the association should succeed
    """
    # Remove default association so we can create it via API.
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id, DBTeamRegion.region_id == test_region.id
    ).delete()
    db.commit()

    response = client.post(
        f"/regions/{test_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team associated with region successfully"
    mock_create_team.assert_awaited_once()
    mock_sync_add_user_to_team.assert_not_awaited()


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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"},
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


@patch("app.api.regions.sync_add_user_to_team", new_callable=AsyncMock)
@patch("app.api.regions.LiteLLMService.create_team", new_callable=AsyncMock)
def test_team_admin_can_associate_public_region_via_team_endpoint(
    mock_create_team,
    mock_sync_add_user_to_team,
    client,
    team_admin_token,
    db,
    test_team,
    test_region,
):
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id, DBTeamRegion.region_id == test_region.id
    ).delete()
    db.commit()

    response = client.post(
        f"/regions/teams/{test_team.id}/regions/{test_region.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team associated with region successfully"
    mock_create_team.assert_awaited_once()
    mock_sync_add_user_to_team.assert_awaited_once()


@patch("app.api.regions.LiteLLMService.create_team", new_callable=AsyncMock)
def test_team_admin_cannot_associate_unscoped_dedicated_region(
    mock_create_team, client, team_admin_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-no-scope",
        label="Dedicated Region No Scope",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/teams/{test_team.id}/regions/{dedicated_region.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )

    assert response.status_code == 403
    assert "Not authorized to assign this dedicated region" in response.json()["detail"]
    mock_create_team.assert_not_awaited()


def test_associate_team_with_non_existent_region(client, admin_token, db, test_team):
    """
    Given an admin user and a non-existent region
    When they try to associate a team with the region
    Then they should receive a 404 error
    """
    response = client.post(
        f"/regions/99999/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.post(
        f"/regions/{dedicated_region.id}/teams/99999",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert "Team not found" in response.json()["detail"]


def test_associate_team_with_already_associated_region(
    client, admin_token, db, test_team
):
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create initial association
    from app.db.models import DBTeamRegion

    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    # Try to associate again
    response = client.post(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion

    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Team disassociated from region successfully"


def test_disassociate_team_from_region_non_admin_fails(
    client, test_token, db, test_team
):
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"},
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


def test_disassociate_team_from_non_existent_association(
    client, admin_token, db, test_team
):
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.delete(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    # Create team-region association
    from app.db.models import DBTeamRegion

    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 1
    assert teams[0]["id"] == test_team.id
    assert teams[0]["name"] == test_team.name


def test_list_regions_for_team(client, team_admin_token, db, test_team, test_region):
    response = client.get(
        f"/regions/teams/{test_team.id}/regions",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )

    assert response.status_code == 200
    region_names = [region["name"] for region in response.json()]
    assert test_region.name in region_names


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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {test_token}"},
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


def test_list_teams_for_non_dedicated_region(client, admin_token, test_region):
    """
    Given an admin user and a non-dedicated region
    When they list teams for the region
    Then the request should succeed
    """
    response = client.get(
        f"/regions/{test_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200


def test_list_teams_for_non_existent_region(client, admin_token):
    """
    Given an admin user
    When they try to list teams for a non-existent region
    Then they should receive a 404 error
    """
    response = client.get(
        "/regions/99999/teams", headers={"Authorization": f"Bearer {admin_token}"}
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
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)

    response = client.get(
        f"/regions/{dedicated_region.id}/teams",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 0


def test_get_team_region_budget_pool_uses_team_budget(
    client, admin_token, db, test_team, test_region
):
    """
    Given a POOL team with purchases
    When requesting region budget
    Then budget is sourced from LiteLLM team budget (pool-level), not per-key defaults.
    """
    test_team.budget_type = "pool"
    db.commit()

    with patch(
        "app.api.regions.LiteLLMService.get_team_info",
        new_callable=AsyncMock,
    ) as mock_get_team_info:
        mock_get_team_info.return_value = {
            "team_info": {"spend": 5.0, "max_budget": 25.0}
        }

        response = client.get(
            f"/regions/{test_region.id}/teams/{test_team.id}/budget",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        mock_get_team_info.assert_awaited_once_with(
            LiteLLMService.format_team_id(test_region.name, test_team.id)
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total_spend"] == 5.0
    assert data["total_budget"] == 20.0


def test_get_team_region_budget_pool_requires_litellm(
    client, admin_token, db, test_team, test_region
):
    """
    Given a POOL team
    When LiteLLM is unavailable
    Then the endpoint should fail (no local fallback for POOL budgets).
    """
    test_team.budget_type = "pool"
    db.commit()

    with patch(
        "app.api.regions.LiteLLMService.get_team_info",
        new_callable=AsyncMock,
    ) as mock_get_team_info:
        mock_get_team_info.side_effect = Exception("LiteLLM unavailable")

        response = client.get(
            f"/regions/{test_region.id}/teams/{test_team.id}/budget",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        mock_get_team_info.assert_awaited_once_with(
            LiteLLMService.format_team_id(test_region.name, test_team.id)
        )

    assert response.status_code == 502
    assert (
        response.json()["detail"] == "Failed to retrieve POOL team budget from LiteLLM"
    )


def test_get_team_model_aliases_allows_team_member_read(
    client, team_key_creator_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-model-aliases-read",
        label="Dedicated Model Aliases Read",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)
    db.add(DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id))
    db.commit()

    with patch(
        "app.api.regions.LiteLLMService.get_team_model_aliases",
        new_callable=AsyncMock,
    ) as mock_get_aliases:
        mock_get_aliases.return_value = {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}
        response = client.get(
            f"/regions/{dedicated_region.id}/teams/{test_team.id}/model-aliases",
            headers={"Authorization": f"Bearer {team_key_creator_token}"},
        )

    assert response.status_code == 200
    assert response.json()["model_aliases"] == {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}
    mock_get_aliases.assert_awaited_once_with(
        LiteLLMService.format_team_id(dedicated_region.name, test_team.id)
    )


def test_get_team_model_aliases_forbidden_for_non_team_member(
    client, test_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-model-aliases-forbidden",
        label="Dedicated Model Aliases Forbidden",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)
    db.add(DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id))
    db.commit()

    response = client.get(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}/model-aliases",
        headers={"Authorization": f"Bearer {test_token}"},
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]


def test_update_team_model_aliases_succeeds_for_team_admin(
    client, team_admin_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-model-aliases-write",
        label="Dedicated Model Aliases Write",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)
    db.add(DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id))
    db.commit()

    with (
        patch(
            "app.api.regions.LiteLLMService.get_model_info",
            new_callable=AsyncMock,
        ) as mock_get_model_info,
        patch(
            "app.api.regions.LiteLLMService.get_team_info",
            new_callable=AsyncMock,
        ) as mock_get_team_info,
        patch(
            "app.api.regions.LiteLLMService.update_team_budget",
            new_callable=AsyncMock,
        ) as mock_update_team_budget,
    ):
        mock_get_model_info.return_value = {
            "data": [{"model_name": "azure/gpt-4-turbo-2024-04-09"}]
        }
        mock_get_team_info.return_value = {
            "team_info": {"max_budget": 20.0, "budget_duration": "1mo"}
        }
        response = client.put(
            f"/regions/{dedicated_region.id}/teams/{test_team.id}/model-aliases",
            headers={"Authorization": f"Bearer {team_admin_token}"},
            json={"model_aliases": {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}},
        )

    assert response.status_code == 200
    assert response.json()["model_aliases"] == {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}
    mock_update_team_budget.assert_awaited_once_with(
        team_id=LiteLLMService.format_team_id(dedicated_region.name, test_team.id),
        max_budget=20.0,
        budget_duration="1mo",
        model_aliases={"gpt-4": "azure/gpt-4-turbo-2024-04-09"},
    )


def test_update_team_model_aliases_rejects_unknown_target_model(
    client, team_admin_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-model-aliases-invalid-target",
        label="Dedicated Model Aliases Invalid Target",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)
    db.add(DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id))
    db.commit()

    with (
        patch(
            "app.api.regions.LiteLLMService.get_model_info",
            new_callable=AsyncMock,
        ) as mock_get_model_info,
        patch(
            "app.api.regions.LiteLLMService.get_team_info",
            new_callable=AsyncMock,
        ) as mock_get_team_info,
        patch(
            "app.api.regions.LiteLLMService.update_team_budget",
            new_callable=AsyncMock,
        ) as mock_update_team_budget,
    ):
        mock_get_model_info.return_value = {"data": [{"model_name": "gpt-4o-mini"}]}
        mock_get_team_info.return_value = {"team_info": {"max_budget": 5.0}}
        response = client.put(
            f"/regions/{dedicated_region.id}/teams/{test_team.id}/model-aliases",
            headers={"Authorization": f"Bearer {team_admin_token}"},
            json={"model_aliases": {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}},
        )

    assert response.status_code == 400
    assert (
        "Alias target model not available in region catalog"
        in response.json()["detail"]
    )
    mock_update_team_budget.assert_not_awaited()


def test_update_team_model_aliases_forbidden_for_key_creator(
    client, team_key_creator_token, db, test_team
):
    dedicated_region = DBRegion(
        name="dedicated-region-model-aliases-write-forbidden",
        label="Dedicated Model Aliases Write Forbidden",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="dedicated-admin",
        postgres_admin_password="dedicated-password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated_region)
    db.commit()
    db.refresh(dedicated_region)
    db.add(DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id))
    db.commit()

    response = client.put(
        f"/regions/{dedicated_region.id}/teams/{test_team.id}/model-aliases",
        headers={"Authorization": f"Bearer {team_key_creator_token}"},
        json={"model_aliases": {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}},
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]
