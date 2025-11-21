import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import patch, Mock, AsyncMock
from app.api.auth import generate_trial_access
from app.core.limit_service import LimitService
from app.db.models import DBUser, DBTeam, DBPrivateAIKey, DBRegion
from app.schemas.models import TrialAccessResponse, Token
from fastapi import Response


@patch("app.api.auth.create_and_set_access_token")
@patch("app.api.auth.create_private_ai_key")
@patch("app.api.teams.register_team")
@patch("app.api.users._create_user_in_db")
@patch("app.core.limit_service.LimitService.get_token_restrictions")
@patch("app.services.litellm.LiteLLMService")
@patch("httpx.AsyncClient")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
@pytest.mark.asyncio
async def test_generate_trial_access(
    mock_litellm_service_class,
    mock_create_user_in_db,
    mock_register_team,
    mock_get_token_restrictions,
    mock_create_token,
    mock_client_class,
    mock_httpx_post_client,
    db: Session,
):
    mock_litellm_instance = mock_litellm_service_class.return_value
    mock_litellm_instance.update_budget = Mock(side_effect=lambda *args, **kwargs: None)
    mock_litellm_instance.delete_key = Mock()

    # Use the httpx POST client fixture
    mock_client_class.return_value = mock_httpx_post_client

    mock_get_token_restrictions.return_value = (30, 10.0, 100)

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_create_user_in_db.return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_team.name = "Trial Team"
    mock_register_team.return_value = mock_team

    mock_key = Mock(spec=DBPrivateAIKey)
    mock_key.id = 1
    mock_key.litellm_token = "test-token"
    mock_key.database_name = "db_test"

    # Mock the return value of create_private_ai_key
    # It returns a DBPrivateAIKey object
    mock_create_private_ai_key_return = Mock(spec=DBPrivateAIKey)
    mock_create_private_ai_key_return.litellm_token = "test-token"
    # We need to patch the return value of the mocked function
    # The second patch in the list is app.api.auth.create_private_ai_key
    # But arguments are passed in reverse order of decorators (excluding pytest.mark)
    # So mock_create_private_ai_key is not explicitly in the args list above?
    # Wait, let's look at the args list:
    # mock_litellm_service_class (patch 6)
    # mock_create_user_in_db (patch 4)
    # mock_register_team (patch 3)
    # mock_get_token_restrictions (patch 5)
    # mock_create_token (patch 1)
    # mock_client_class (patch 7)
    # mock_httpx_post_client (patch 8)

    # Missing args for:
    # patch 2: app.api.auth.create_private_ai_key

    # Let's redefine the args to be explicit and correct
    pass

@patch("app.api.auth.create_and_set_access_token")
@patch("app.api.auth.create_private_ai_key", new_callable=AsyncMock)
@patch("app.api.auth.register_team", new_callable=AsyncMock)
@patch("app.api.auth._create_user_in_db")
@patch("app.core.limit_service.LimitService.get_token_restrictions")
@patch("app.api.auth.LiteLLMService")
@patch("httpx.AsyncClient")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
@pytest.mark.asyncio
async def test_generate_trial_access(
    mock_client_class,
    mock_litellm_service_class,
    mock_get_token_restrictions,
    mock_create_user_in_db,
    mock_register_team,
    mock_create_private_ai_key,
    mock_create_token,
    db: Session,
):
    # Mock DB Session
    mock_db = Mock(spec=Session)

    # Mock DBRegion query
    mock_region = Mock(spec=DBRegion)
    mock_region.id = 1
    mock_region.litellm_api_url = "http://test"
    mock_region.litellm_api_key = "test"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_region

    mock_litellm_instance = mock_litellm_service_class.return_value
    mock_litellm_instance.update_budget = AsyncMock(return_value=None)
    mock_litellm_instance.delete_key = AsyncMock(return_value=True)

    # Mock LimitService
    mock_limit_service = Mock(spec=LimitService)
    mock_limit_service.get_token_restrictions.return_value = (30, 10.0, 100)

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_user.is_admin = False
    mock_user.is_active = True
    mock_user.role = "admin"
    mock_create_user_in_db.return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_team.name = "Trial Team"
    mock_register_team.return_value = mock_team

    mock_key = Mock(spec=DBPrivateAIKey)
    mock_key.id = 1
    mock_key.litellm_token = "test-token"
    mock_key.database_name = "db_test"
    mock_key.team_id = 12
    mock_key.owner_id = 1
    mock_key.region = "local"
    mock_key.created_at = "2023-01-01T00:00:00"
    mock_key.litellm_api_url = "http://litellm:4000"
    mock_key.database_host = "postgres"
    mock_key.database_username = "user"
    mock_key.database_password = "password"
    mock_key.name = "test-key"
    mock_create_private_ai_key.return_value = mock_key

    mock_create_token.return_value = Token(access_token="mock-jwt-token", token_type="bearer")

    # Mock Response object
    mock_response = Mock(spec=Response)

    result = await generate_trial_access(mock_response, mock_db, mock_limit_service)

    assert result.user.id == 1
    assert result.team_id == 12
    assert result.key.litellm_token == "test-token"

    mock_litellm_instance.update_budget.assert_called_once()


@patch("app.api.auth.create_private_ai_key", new_callable=AsyncMock)
@patch("app.api.auth.register_team", new_callable=AsyncMock)
@patch("app.api.auth._create_user_in_db")
@patch("httpx.AsyncClient")
@patch("app.core.config.settings.DEFAULT_AI_TOKEN_REGION", "test-region")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
@pytest.mark.asyncio
async def test_generate_trial_access_cleanup_on_key_creation_failure(
    mock_client_class,
    mock_create_user,
    mock_register_team,
    mock_create_private_ai_key,
    db: Session,
):
    """
    Given create_private_ai_key fails
    When a trial access is generated
    Then User and Team should be deleted (rolled back)
    """
    # Mock DB Session
    mock_db = Mock(spec=Session)

    # Mock DBRegion query
    mock_region = Mock(spec=DBRegion)
    mock_region.id = 1
    mock_region.litellm_api_url = "http://test"
    mock_region.litellm_api_key = "test"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_region

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_create_user.return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_register_team.return_value = mock_team

    # Simulate failure
    mock_create_private_ai_key.side_effect = Exception("Key creation failed")

    # Mock LimitService
    mock_limit_service = Mock(spec=LimitService)

    # Mock Response object
    mock_response = Mock(spec=Response)

    with pytest.raises(HTTPException) as exc_info:
        await generate_trial_access(mock_response, mock_db, mock_limit_service)

    assert exc_info.value.status_code == 500

    assert mock_db.delete.call_count >= 2
