import pytest
from unittest.mock import patch, AsyncMock
from app.db.models import DBRegion, DBTeamRegion


@pytest.fixture
def dedicated_region(db):
    """Fixture to create a dedicated region for testing"""
    region = DBRegion(
        name="dedicated-test-region",
        label="Dedicated Test Region",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="admin",
        postgres_admin_password="password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.LiteLLMService", autospec=True)
async def test_create_key_dedicated_region_with_access(
    mock_litellm_service_class,
    client,
    test_team,
    team_admin_token,
    dedicated_region,
    db,
):
    """
    Given a team with access to a dedicated region
    When creating a LiteLLM token in that region
    Then the team should be bootstrapped in LiteLLM and the token created successfully
    """
    # Setup access
    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    # Mock LiteLLM service
    mock_service = mock_litellm_service_class.return_value
    mock_service.create_key = AsyncMock(return_value="test-token")
    mock_service.create_team = AsyncMock(return_value=None)

    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"region_id": dedicated_region.id, "name": "Dedicated Key"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["litellm_token"] == "test-token"

    # Verify team was created/bootstrapped in LiteLLM for this dedicated region
    mock_service.create_team.assert_awaited_once()
    # Verify key was created
    mock_service.create_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_key_dedicated_region_without_access(
    client, test_team, team_admin_token, dedicated_region, db
):
    """
    Given a team WITHOUT access to a dedicated region
    When attempting to create a LiteLLM token in that region
    Then the request should fail with a 403 Forbidden error
    """
    # Ensure no access association exists
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id,
        DBTeamRegion.region_id == dedicated_region.id,
    ).delete()
    db.commit()

    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"region_id": dedicated_region.id, "name": "No Access Key"},
    )

    assert response.status_code == 403
    assert "does not have access to dedicated region" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.PostgresManager", autospec=True)
async def test_create_vector_db_dedicated_region_with_access(
    mock_postgres_manager_class,
    client,
    test_team,
    team_admin_token,
    dedicated_region,
    db,
):
    """
    Given a team with access to a dedicated region
    When creating a vector DB in that region
    Then the database should be created successfully
    """
    # Setup access
    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    # Mock PostgresManager
    mock_manager = mock_postgres_manager_class.return_value
    mock_manager.create_database = AsyncMock(
        return_value={
            "database_name": "test_db",
            "database_host": "host",
            "database_username": "user",
            "database_password": "pass",
        }
    )

    response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"region_id": dedicated_region.id, "name": "Dedicated Vector DB"},
    )

    assert response.status_code == 200
    # Verify database was created via PostgresManager
    mock_manager.create_database.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_vector_db_dedicated_region_without_access(
    client, test_team, team_admin_token, dedicated_region, db
):
    """
    Given a team WITHOUT access to a dedicated region
    When attempting to create a vector DB in that region
    Then the request should fail with a 403 Forbidden error
    """
    # Ensure no access association exists
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id,
        DBTeamRegion.region_id == dedicated_region.id,
    ).delete()
    db.commit()

    response = client.post(
        "/private-ai-keys/vector-db",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"region_id": dedicated_region.id, "name": "No Access Vector DB"},
    )

    assert response.status_code == 403
    assert "does not have access to dedicated region" in response.json()["detail"]


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.LiteLLMService", autospec=True)
async def test_system_admin_access_to_any_dedicated_region(
    mock_litellm_service_class, client, admin_token, dedicated_region, test_team, db
):
    """
    Given a system admin
    When creating a key for a team in a dedicated region (even if team doesn't have explicit access yet)
    Then it should fail because the access check is strict for teams
    But if the admin gives access first, it works.
    Actually, system admins are also bound by the access check logic I implemented.
    Let's verify that even an admin must ensure the team has access.
    """
    # Mock LiteLLM service
    mock_service = mock_litellm_service_class.return_value
    mock_service.create_key = AsyncMock(return_value="admin-token")
    mock_service.create_team = AsyncMock(return_value=None)

    # Attempt without access (should fail)
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": dedicated_region.id,
            "name": "Admin Key",
            "team_id": test_team.id,
        },
    )
    assert response.status_code == 403

    # Grant access
    team_region = DBTeamRegion(team_id=test_team.id, region_id=dedicated_region.id)
    db.add(team_region)
    db.commit()

    # Attempt with access (should succeed)
    response = client.post(
        "/private-ai-keys/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "region_id": dedicated_region.id,
            "name": "Admin Key",
            "team_id": test_team.id,
        },
    )
    assert response.status_code == 200
    assert response.json()["litellm_token"] == "admin-token"
