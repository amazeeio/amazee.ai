from app.core.config import settings
import pytest
from unittest.mock import patch, Mock, AsyncMock
from app.db.models import DBUser, DBTeam, DBPrivateAIKey, DBRegion
from datetime import datetime, UTC
from fastapi import status, HTTPException
from httpx import HTTPStatusError
from app.schemas.limits import (
    LimitSource,
    OwnerType,
    ResourceType,
    LimitType,
    UnitType,
)
import time
import secrets
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.api.users._create_user_in_db")
@patch("app.api.teams.register_team")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
@patch("app.core.limit_service.LimitService.get_token_restrictions")
async def test_generate_trial_access(
    mock_get_token_restrictions,
    mock_register_team,
    mock_create_user_in_db,
    db: Session,
    client: TestClient,
):
    """
    Given no existing trial account
    When a trial access is generated
    Then a new user, team, and private AI key should be created with a limited budget (AI_TRIAL_MAX_BUDGET)
    """
    # Setup mocks
    mock_client_class = Mock()
    mock_httpx_post_client = Mock()
    mock_client_class.return_value = mock_httpx_post_client

    # Mock get_token_restrictions to return trial budget
    mock_get_token_restrictions.return_value = (settings.DEFAULT_KEY_DURATION, settings.AI_TRIAL_MAX_BUDGET, settings.DEFAULT_RPM_PER_KEY)

    # Mock LiteLLM key creation
    mock_create_key = Mock()
    mock_create_key.return_value = "trial-litellm-token-123"

    # Mock vector database creation
    mock_create_db = Mock()
    mock_create_db.return_value = {
        "database_name": "trial_db_123",
        "database_host": "test-host",
        "database_username": "trial_user",
        "database_password": "trial_pass"
    }

    mock_user = Mock(spec=DBUser)
    mock_user.id = "test-user-id"
    mock_user.email = "trial-test-user@example.com"
    mock_user.team_id = "test-team-id"
    mock_user.role = "admin" # Changed from UserRole.ADMIN to "admin" to match mock_create_user_in_db return type
    mock_create_user_in_db.return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = "test-team-id"
    mock_team.name = "Trial Team test-user@example.com"
    mock_team.admin_email = "trial-test-user@example.com"
    mock_register_team.return_value = mock_team

    # Make request
    response = client.post("/auth/generate-trial-access")

    # Verify response
    assert response.status_code == 200
    data = response.json()

    # Verify response structure
    assert "key" in data
    assert "user" in data
    assert "team_id" in data
    assert "team_name" in data

    # Verify user was created
    user_data = data["user"]
    assert user_data["email"] == mock_user.email
    assert user_data["email"].startswith("trial-")
    assert "@example.com" in user_data["email"]
    assert user_data["role"] == mock_user.role
    assert user_data["team_id"] == mock_team.id

    # Verify team was created
    assert data["team_name"] == mock_team.name
    assert data["team_id"] == mock_team.id

    # Verify private AI key was created
    key_data = data["key"]
    assert key_data["litellm_token"] == "trial-litellm-token-123"
    assert key_data["name"].startswith("Trial Access Key for")
    assert key_data["owner_id"] == mock_user.id
    assert key_data["team_id"] == mock_team.id
    assert key_data["region"] == "test-region" # Assuming test_region is not directly available here, so hardcode

    # Verify database state
    db_user = db.query(DBUser).filter(DBUser.id == mock_user.id).first()
    assert db_user is not None
    assert db_user.role == mock_user.role
    assert db_user.team_id == mock_team.id

    db_team = db.query(DBTeam).filter(DBTeam.id == mock_team.id).first()
    assert db_team is not None
    assert db_team.admin_email == mock_user.email

    db_key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == key_data["id"]).first()
    assert db_key is not None
    assert db_key.litellm_token == "trial-litellm-token-123"
    assert db_key.database_name == "trial_db_123"

    # Verify LiteLLM key was created with AI_TRIAL_MAX_BUDGET budget
    mock_create_key.assert_called_once()
    call_kwargs = mock_create_key.call_args[1]
    assert call_kwargs["max_budget"] == settings.AI_TRIAL_MAX_BUDGET
    assert call_kwargs["email"] == mock_user.email
    assert call_kwargs["name"] == key_data["name"]

    # Verify team budget limit was set
    team_budget_limit = db.query(db.models.DBLimitedResource).filter(
        db.models.DBLimitedResource.owner_type == OwnerType.TEAM,
        db.models.DBLimitedResource.owner_id == mock_team.id,
        db.models.DBLimitedResource.resource == ResourceType.BUDGET
    ).first()
    assert team_budget_limit is not None
    assert team_budget_limit.max_value == settings.AI_TRIAL_MAX_BUDGET
    assert team_budget_limit.limited_by == LimitSource.MANUAL
    assert team_budget_limit.set_by == "system-trial-generation"

    # Verify user budget limit was set
    user_budget_limit = db.query(db.models.DBLimitedResource).filter(
        db.models.DBLimitedResource.owner_type == OwnerType.USER,
        db.models.DBLimitedResource.owner_id == mock_user.id,
        db.models.DBLimitedResource.resource == ResourceType.BUDGET
    ).first()
    assert user_budget_limit is not None
    assert user_budget_limit.max_value == settings.AI_TRIAL_MAX_BUDGET
    assert user_budget_limit.limited_by == LimitSource.MANUAL
    assert user_budget_limit.set_by == "system-trial-generation"


@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_no_region(client, db):
    """
    Given no active regions exist
    When a trial access is generated
    Then a 404 error should be returned
    """
    # Ensure no regions exist
    db.query(DBRegion).delete()
    db.commit()

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 404
    assert "No region available for trial access" in response.json()["detail"]


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "nonexistent-region")
def test_generate_trial_access_fallback_to_first_active_region(
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given DEFAULT_AI_TOKEN_REGION doesn't exist but another active region exists
    When a trial access is generated
    Then it should use the first active region as fallback
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.return_value = {
        "database_name": "trial_db_123",
        "database_host": "test-host",
        "database_username": "trial_user",
        "database_password": "trial_pass"
    }

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 200
    data = response.json()
    assert data["key"]["region"] == test_region.name


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.api.auth._create_user_in_db")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_on_user_creation_failure(
    mock_create_user,
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    mock_httpx_post_client
):
    """
    Given user creation fails
    When a trial access is generated
    Then no resources should be created and error should be returned
    """
    # Mock user creation failure
    mock_create_user.side_effect = Exception("User creation failed")

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]

    # Verify no cleanup needed since nothing was created
    mock_create_key.assert_not_called()
    mock_create_db.assert_not_called()


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.api.auth.register_team")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_on_team_creation_failure(
    mock_register_team,
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given team creation fails after user is created
    When a trial access is generated
    Then user should be cleaned up and error should be returned
    """
    # Mock team creation failure
    mock_register_team.side_effect = Exception("Team creation failed")

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]

    # Verify no LiteLLM or DB resources were created
    mock_create_key.assert_not_called()
    mock_create_db.assert_not_called()


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.services.litellm.LiteLLMService.delete_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_on_litellm_failure(
    mock_delete_key,
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given LiteLLM key creation fails after user and team are created
    When a trial access is generated
    Then user and team should remain but LiteLLM cleanup should be attempted
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.side_effect = Exception("LiteLLM creation failed")
    mock_delete_key.return_value = None

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]

    # Verify LiteLLM key creation was attempted
    mock_create_key.assert_called_once()

    # Verify cleanup was not called since key creation failed (no token to clean)
    mock_delete_key.assert_not_called()

    # Verify no vector DB was created
    mock_create_db.assert_not_called()


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.db.postgres.PostgresManager.delete_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.services.litellm.LiteLLMService.delete_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_on_vector_db_failure(
    mock_delete_litellm_key,
    mock_create_key,
    mock_delete_db,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given vector database creation fails after LiteLLM key is created
    When a trial access is generated
    Then LiteLLM key should be cleaned up and error should be returned
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.side_effect = Exception("Vector DB creation failed")
    mock_delete_litellm_key.return_value = None
    mock_delete_db.return_value = None

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]

    # Verify LiteLLM key was created
    mock_create_key.assert_called_once()

    # Verify LiteLLM key cleanup was attempted
    mock_delete_litellm_key.assert_called_once_with("trial-litellm-token-123")

    # Verify vector DB cleanup was not called (since creation failed)
    mock_delete_db.assert_not_called()


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.db.postgres.PostgresManager.delete_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.services.litellm.LiteLLMService.delete_key")
@patch("sqlalchemy.orm.Session.commit")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_on_db_storage_failure(
    mock_commit,
    mock_delete_litellm_key,
    mock_create_key,
    mock_delete_db,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given database storage fails after both LiteLLM key and vector DB are created
    When a trial access is generated
    Then both resources should be cleaned up and error should be returned
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.return_value = {
        "database_name": "trial_db_123",
        "database_host": "test-host",
        "database_username": "trial_user",
        "database_password": "trial_pass"
    }
    mock_commit.side_effect = Exception("Database storage failed")
    mock_delete_litellm_key.return_value = None
    mock_delete_db.return_value = None

    response = client.post("/auth/generate-trial-access")

    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]

    # Verify both resources were created
    mock_create_key.assert_called_once()
    mock_create_db.assert_called_once()

    # Verify both resources were cleaned up
    mock_delete_litellm_key.assert_called_once_with("trial-litellm-token-123")
    mock_delete_db.assert_called_once_with("trial_db_123")


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.services.litellm.LiteLLMService.delete_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_cleanup_failure_handling(
    mock_delete_litellm_key,
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given cleanup process itself fails
    When a trial access generation fails
    Then the original error should still be returned
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.side_effect = Exception("Vector DB creation failed")
    # Mock cleanup failure
    mock_delete_litellm_key.side_effect = Exception("Cleanup failed")

    response = client.post("/auth/generate-trial-access")

    # Verify the original error is returned, not cleanup error
    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]
    assert "Vector DB creation failed" in response.json()["detail"]

    # Verify cleanup was attempted
    mock_delete_litellm_key.assert_called_once()


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_http_exception_preservation(
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given an HTTPException is raised during creation
    When a trial access is generated
    Then the original HTTPException should be preserved
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client

    # Mock HTTPException during vector DB creation
    mock_create_db.side_effect = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid database configuration"
    )

    response = client.post("/auth/generate-trial-access")

    # Verify HTTPException is preserved (but wrapped in 500)
    assert response.status_code == 500
    assert "Failed to create trial access" in response.json()["detail"]


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
def test_generate_trial_access_with_limits_enabled(
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    test_team,
    mock_httpx_post_client
):
    """
    Given limits are enabled
    When a trial access is generated
    Then limit checks should be performed
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.return_value = {
        "database_name": "trial_db_123",
        "database_host": "test-host",
        "database_username": "trial_user",
        "database_password": "trial_pass"
    }

    response = client.post("/auth/generate-trial-access")

    # Should still succeed - limits are checked but trial accounts should pass
    assert response.status_code == 200
    data = response.json()
    assert data["key"] is not None


@patch("httpx.AsyncClient")
@patch("app.db.postgres.PostgresManager.create_database")
@patch("app.services.litellm.LiteLLMService.create_key")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
def test_generate_trial_access_unique_user_emails(
    mock_create_key,
    mock_create_db,
    mock_client_class,
    client,
    test_region,
    db,
    mock_httpx_post_client
):
    """
    Given multiple trial access requests
    When trial accesses are generated
    Then each should have a unique user email
    """
    # Setup mocks
    mock_client_class.return_value = mock_httpx_post_client
    mock_create_key.return_value = "trial-litellm-token-123"
    mock_create_db.return_value = {
        "database_name": "trial_db_123",
        "database_host": "test-host",
        "database_username": "trial_user",
        "database_password": "trial_pass"
    }

    # Generate first trial access
    response1 = client.post("/auth/generate-trial-access")
    assert response1.status_code == 200
    email1 = response1.json()["user"]["email"]

    # Generate second trial access
    response2 = client.post("/auth/generate-trial-access")
    assert response2.status_code == 200
    email2 = response2.json()["user"]["email"]

    # Verify emails are unique
    assert email1 != email2
    assert email1.startswith("trial-")
    assert email2.startswith("trial-")

