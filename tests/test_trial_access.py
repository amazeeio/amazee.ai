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
    with (
        patch(
            "app.api.private_ai_keys._create_private_ai_key", new_callable=AsyncMock
        ) as mock_create_key,
        patch(
            "app.api.auth.register_team", new_callable=AsyncMock
        ) as mock_register_team,
        patch(
            "app.api.auth._create_user_in_db", new_callable=AsyncMock
        ) as mock_create_user,
        patch("httpx.AsyncClient") as mock_httpx_client_cls,
        patch("app.api.auth.LiteLLMService") as mock_litellm_service_cls,
        patch("app.api.auth.create_and_set_access_token") as mock_create_token,
        patch(
            "app.core.limit_service.LimitService.get_token_restrictions"
        ) as mock_get_token_restrictions,
        patch("app.core.config.settings.AI_TRIAL_REGION", "test-region"),
        patch("app.core.config.settings.ENABLE_LIMITS", True),
    ):
        # Setup common mock behaviors
        mock_create_token.return_value = Token(
            access_token="mock-jwt-token", token_type="bearer"
        )
        mock_get_token_restrictions.return_value = (30, 10.0, 100)

        yield {
            "create_key": mock_create_key,
            "register_team": mock_register_team,
            "create_user": mock_create_user,
            "httpx": mock_httpx_client_cls,
            "litellm_cls": mock_litellm_service_cls,
            "create_token": mock_create_token,
            "get_token_restrictions": mock_get_token_restrictions,
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
        # `is`, not `==`: the trial-cap count query passes a SQLAlchemy
        # expression here, and `==` on it builds a (failing) SQL comparison.
        mock_query = Mock()
        if model is DBRegion:
            mock_query.filter.return_value.first.return_value = mock_region
        elif model is DBTeam:
            # Force create team; the endpoint locks the row FOR UPDATE
            mock_query.filter.return_value.with_for_update.return_value.first.return_value = None
        else:
            mock_query.filter.return_value.first.return_value = None
            mock_query.filter.return_value.scalar.return_value = 0
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
        "updated_at": "2024-01-01T00:00:00",
    }
    mock_limit_service.set_limit.return_value = valid_limit

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_user.is_admin = False
    mock_user.is_active = True
    mock_user.role = "admin"
    mock_user.team_id = 12
    mock_user.receive_marketing_updates = False
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

    result = await generate_trial_access(
        Mock(), mock_response, mock_db, mock_limit_service
    )

    assert result.user.id == 1
    assert result.team_id == 12
    assert result.key.litellm_token == "test-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_error,expected_status",
    [
        (Exception("Key creation failed"), 500),
        # HTTPExceptions keep their status code AND still clean up the
        # already-committed trial user (orphans consume trial capacity).
        (HTTPException(status_code=503, detail="LiteLLM unavailable"), 503),
    ],
)
async def test_generate_trial_access_cleanup_on_key_creation_failure(
    mock_auth_deps,
    db: Session,
    key_error,
    expected_status,
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
        # `is`, not `==`: the trial-cap count query passes a SQLAlchemy
        # expression here, and `==` on it builds a (failing) SQL comparison.
        q = Mock()
        if model is DBRegion:
            q.filter.return_value.first.return_value = mock_region
        elif model is DBTeam:
            # Force create team; the endpoint locks the row FOR UPDATE
            q.filter.return_value.with_for_update.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.scalar.return_value = 0
        return q

    mock_db.query.side_effect = get_mock_query

    mock_user = Mock(spec=DBUser)
    mock_user.id = 1
    mock_user.email = "trial-user@example.com"
    mock_user.receive_marketing_updates = False
    mock_auth_deps["create_user"].return_value = mock_user

    mock_team = Mock(spec=DBTeam)
    mock_team.id = 12
    mock_team.set_by_context = "anonymous-trial-generation"
    mock_auth_deps["register_team"].return_value = mock_team

    # Simulate failure
    mock_auth_deps["create_key"].side_effect = key_error

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
        "updated_at": "2024-01-01T00:00:00",
    }
    mock_limit_service.set_limit.return_value = valid_limit

    # Mock Response object
    mock_response = Mock(spec=Response)

    with pytest.raises(HTTPException) as exc_info:
        await generate_trial_access(Mock(), mock_response, mock_db, mock_limit_service)

    assert exc_info.value.status_code == expected_status

    assert mock_db.delete.call_count >= 1


@patch("app.db.postgres.PostgresManager.create_database")
@patch("httpx.AsyncClient")
def test_trial_key_cannot_read_its_own_litellm_team(
    mock_client_class, mock_create_db, client, db, test_region
):
    """Full provisioning path.

    Every anonymous trial shares one team, and LiteLLM treats a key's team_id as
    team membership — so an unscoped trial key can GET /team/info and read every
    other trial's owner, spend and budget. The trial key must therefore be minted
    with LiteLLM's inference-only route group.
    """
    mock_create_db.return_value = {
        "database_name": "db_trial",
        "database_host": "pghost",
        "database_username": "user_trial",
        "database_password": "pw",
    }

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"key": "sk-trial-test-key"}
    mock_response.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    with patch("app.core.config.settings.AI_TRIAL_REGION", test_region.name):
        response = client.post("/auth/generate-trial-access", json={})

    assert response.status_code == 200, response.text

    key_generate_calls = [
        call
        for call in mock_client.post.call_args_list
        if str(call.args[0]).endswith("/key/generate")
    ]
    assert len(key_generate_calls) == 1
    # Spelled out rather than compared against INFERENCE_ONLY_ROUTES: the
    # constant builds the request, so comparing to it would accept any route
    # added to it later, including a management route. /model/info is required
    # because the Drupal module lists models through it and llm_api_routes does
    # not cover it; anything beyond these two must fail this test.
    assert key_generate_calls[0].kwargs["json"]["allowed_routes"] == [
        "llm_api_routes",
        "/model/info",
    ]
