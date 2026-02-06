import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from unittest.mock import Mock, AsyncMock, patch
from app.api.auth import generate_trial_access
from app.core.limit_service import LimitService
from app.db.models import DBUser, DBTeam, DBPrivateAIKey, DBRegion
from app.schemas.models import Token
from fastapi import Response


@pytest.fixture
def mock_auth_deps():
    """Fixture that bundles all auth dependency mocks."""
    with patch("app.api.auth.create_private_ai_key", new_callable=AsyncMock) as mock_create_key, \
         patch("app.api.auth.register_team", new_callable=AsyncMock) as mock_register_team, \
         patch("app.api.auth._create_user_in_db") as mock_create_user, \
         patch("httpx.AsyncClient") as mock_httpx_client_cls, \
         patch("app.api.auth.LiteLLMService") as mock_litellm_service_cls, \
         patch("app.api.auth.create_and_set_access_token") as mock_create_token, \
         patch("app.core.limit_service.LimitService.get_token_restrictions") as mock_get_token_restrictions, \
         patch("app.core.config.settings.AI_TRIAL_REGION", "test-region"), \
         patch("app.core.config.settings.ENABLE_LIMITS", True):

        # Setup common mock behaviors
        mock_create_token.return_value = Token(access_token="mock-jwt-token", token_type="bearer")
        mock_get_token_restrictions.return_value = (30, 10.0, 100)

        yield {
            "create_key": mock_create_key,
            "register_team": mock_register_team,
            "create_user": mock_create_user,
            "httpx": mock_httpx_client_cls,
            "litellm_cls": mock_litellm_service_cls,
            "create_token": mock_create_token,
            "get_token_restrictions": mock_get_token_restrictions
        }


@pytest.mark.asyncio
async def test_generate_trial_access(mock_auth_deps, db: Session):
    # Mock DB Session
    mock_db = Mock(spec=Session)

    # Mock DBRegion, DBTeam query results
    mock_region = Mock(spec=DBRegion)
    mock_region.id = 1
    mock_region.litellm_api_url = "http://test"
    mock_region.litellm_api_key = "test"
    mock_region.name = "test-region"
    mock_region.label = "Test Region"

    def get_mock_query(model):
        mock_query = Mock()
        if model == DBRegion:
            mock_query.filter.return_value.first.return_value = mock_region
        elif model == DBTeam:
            mock_query.filter.return_value.first.return_value = None # Force create team
        elif model == DBUser:
            mock_query.filter.return_value.first.return_value = None
        else:
            mock_query.filter.return_value.first.return_value = None
        return mock_query

    mock_db.query.side_effect = get_mock_query

    # Mock LimitService
    mock_limit_service = Mock(spec=LimitService)
    mock_limit_service.get_token_restrictions.return_value = (30, 10.0, 100)

    valid_limit = {
        "id": 1,
        "owner_type": "user",
        "owner_id": 1,
        "resource": "max_budget",
        "limit_type": "data_plane",
        "unit": "dollar",
        "max_value": 10.0,
        "current_value": 0.0,
        "limited_by": "manual",
        "set_by": "test",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }
    mock_limit_service.set_limit.return_value = valid_limit

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_user.is_admin = False
    mock_user.is_active = True
    mock_user.role = "admin"
    mock_user.team_id = 12
    mock_auth_deps["create_user"].return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_team.name = "Trial Team"
    mock_auth_deps["register_team"].return_value = mock_team

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
    mock_auth_deps["create_key"].return_value = mock_key

    # Mock Response object
    mock_response = Mock(spec=Response)

    result = await generate_trial_access(mock_response, mock_db, mock_limit_service)

    assert result.user.id == 1
    assert result.team_id == 12
    assert result.key.litellm_token == "test-token"


@pytest.mark.asyncio
async def test_generate_trial_access_cleanup_on_key_creation_failure(
    mock_auth_deps,
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

    def get_mock_query(model):
        q = Mock()
        if model == DBRegion:
            q.filter.return_value.first.return_value = mock_region
        elif model == DBTeam:
            q.filter.return_value.first.return_value = None # Force create team
        elif model == DBUser:
             q.filter.return_value.first.return_value = None
        else:
             q.filter.return_value.first.return_value = None
        return q

    mock_db.query.side_effect = get_mock_query

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_auth_deps["create_user"].return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_team.set_by_context = "anonymous-trial-generation"
    mock_auth_deps["register_team"].return_value = mock_team

    # Simulate failure
    mock_auth_deps["create_key"].side_effect = Exception("Key creation failed")

    # Mock LimitService
    mock_limit_service = Mock(spec=LimitService)
    valid_limit = {
        "id": 1,
        "owner_type": "user",
        "owner_id": 1,
        "resource": "max_budget",
        "limit_type": "data_plane",
        "unit": "dollar",
        "max_value": 10.0,
        "current_value": 0.0,
        "limited_by": "manual",
        "set_by": "test",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    }
    mock_limit_service.set_limit.return_value = valid_limit

    # Mock Response object
    mock_response = Mock(spec=Response)

    with pytest.raises(HTTPException) as exc_info:
        await generate_trial_access(mock_response, mock_db, mock_limit_service)

    assert exc_info.value.status_code == 500

    assert mock_db.delete.call_count >= 1
