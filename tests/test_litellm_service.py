import pytest
import asyncio
import hashlib
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, Mock
from fastapi import HTTPException
from app.services.litellm import LiteLLMService
from httpx import HTTPStatusError
import httpx


@pytest.fixture
def mock_litellm_response():
    return {"key": "test-private-key-123"}


@pytest.fixture
def mock_httpx_failure_client():
    """Mock httpx.AsyncClient for operations that should fail"""

    def _create_failure_client(status_code, error_message):
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = HTTPStatusError(
            error_message, request=None, response=None
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.get.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        return mock_client

    return _create_failure_client


def test_init_with_valid_parameters(test_region):
    """Test LiteLLMService initialization with valid parameters"""
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    assert service.api_url == test_region.litellm_api_url
    assert service.master_key == test_region.litellm_api_key


def test_init_with_empty_api_url():
    """Test LiteLLMService initialization with empty API URL"""
    with pytest.raises(ValueError, match="LiteLLM API URL is required"):
        LiteLLMService(api_url="", api_key="test-key")


def test_init_with_empty_api_key():
    """Test LiteLLMService initialization with empty API key"""
    with pytest.raises(ValueError, match="LiteLLM API key is required"):
        LiteLLMService(api_url="https://test.com", api_key="")


@patch("httpx.AsyncClient")
def test_create_key_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful key creation"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(
        service.create_key(
            email="test@example.com", name="Test Key", user_id=123, team_id="team-456"
        )
    )

    assert result == "test-private-key-123"
    mock_httpx_post_client.post.assert_called_once()
    # Verify key_alias was sanitized ("email - name" format)
    call_args = mock_httpx_post_client.post.call_args
    assert call_args.kwargs["json"]["key_alias"] == "test_at_example.com_-_Test_Key"


@patch("httpx.AsyncClient")
def test_create_key_with_email_fallback(
    mock_client_class, test_region, mock_httpx_post_client
):
    """Test key creation with email fallback for key_alias"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # Use empty name to trigger email fallback in actual_name
    result = asyncio.run(
        service.create_key(
            email="test@example.com", name="", user_id=123, team_id="team-456"
        )
    )

    assert result == "test-private-key-123"
    mock_httpx_post_client.post.assert_called_once()
    # Verify key_alias was sanitized ("email - fallback_name" format)
    call_args = mock_httpx_post_client.post.call_args
    assert call_args.kwargs["json"]["key_alias"] == "test_at_example.com_-_key-123"


@patch("httpx.AsyncClient")
def test_create_key_can_create_blocked_key(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.create_key(
            email="test@example.com",
            name="Test Key",
            user_id=123,
            team_id="team-456",
            blocked=True,
        )
    )

    call_args = mock_httpx_post_client.post.call_args
    assert call_args.kwargs["json"]["blocked"] is True


@patch("app.core.config.settings.ENABLE_LIMITS", True)
def test_create_key_with_limits_requires_non_none_values(test_region):
    """Test create_key validates required per-key limits when apply_limits is True."""
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(
            service.create_key(
                email="test@example.com",
                name="Test Key",
                user_id=123,
                team_id="team-456",
                duration=None,
                max_budget=100.0,
                rpm_limit=500,
                apply_limits=True,
            )
        )

    assert (
        str(exc_info.value)
        == "duration, max_budget, and rpm_limit are required when apply_limits=True"
    )


@patch("httpx.AsyncClient")
def test_create_key_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key creation failure"""
    mock_client_class.return_value = mock_httpx_failure_client(
        500, "Internal Server Error"
    )

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            service.create_key(
                email="test@example.com",
                name="Test Key",
                user_id=123,
                team_id="team-456",
            )
        )

    assert exc_info.value.status_code == 500
    assert "Failed to create LiteLLM key" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_delete_key_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful key deletion"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("test-token"))

    assert result is True
    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/delete",
        json={"keys": ["test-token"]},
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
    )


@patch("httpx.AsyncClient")
def test_delete_key_not_found(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """Test key deletion when key not found (should return True)"""
    mock_client_class.return_value = mock_httpx_failure_client(404, "Not Found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("non-existent-token"))

    assert result is True


@patch("httpx.AsyncClient")
def test_delete_key_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key deletion failure"""
    mock_client_class.return_value = mock_httpx_failure_client(
        500, "Internal Server Error"
    )

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.delete_key("test-token"))

    assert exc_info.value.status_code == 500
    assert "Failed to delete LiteLLM key" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_get_key_info_success(mock_client_class, test_region, mock_httpx_get_client):
    """Test successful key info retrieval"""
    mock_client_class.return_value = mock_httpx_get_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.get_key_info("test-token"))

    expected_response = {
        "info": {
            "spend": 10.5,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z",
        }
    }
    assert result == expected_response
    mock_httpx_get_client.get.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/info",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        params={"key": "test-token"},
    )


@patch("httpx.AsyncClient")
def test_get_key_info_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """Test key info retrieval failure"""
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"error": {"message": "Not Found"}}
    request = httpx.Request("GET", f"{test_region.litellm_api_url}/key/info")
    response = httpx.Response(
        status_code=404, request=request, json={"error": {"message": "Not Found"}}
    )
    mock_response.raise_for_status.side_effect = HTTPStatusError(
        "Not Found", request=request, response=response
    )

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.get_key_info("test-token"))

    assert exc_info.value.status_code == 404
    assert "Failed to get LiteLLM key information" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_budget_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful budget update"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_budget("test-token", "monthly", 100.0))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": "monthly",
            "duration": "365d",
            "max_budget": 100.0,
        },
    )


@patch("httpx.AsyncClient")
def test_update_budget_zero_amount_sends_max_budget(
    mock_client_class, test_region, mock_httpx_post_client
):
    """Test that budget_amount=0.0 still includes max_budget in the request payload"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(service.update_budget("test-token", "monthly", 0.0))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": "monthly",
            "duration": "365d",
            "max_budget": 0.0,
        },
    )


@patch("httpx.AsyncClient")
def test_update_budget_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """Test budget update failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_budget("test-token", "monthly", 100.0))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM budget" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_key_budget_does_not_override_duration(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_key_budget(
            litellm_token="test-token",
            budget_duration="1mo",
            max_budget=10.0,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": "1mo",
            "max_budget": 10.0,
        },
    )


@patch("httpx.AsyncClient")
def test_update_key_budget_clear_max_budget_keeps_existing_duration(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_key_budget(
            litellm_token="test-token",
            budget_duration=None,
            max_budget=None,
            clear_max_budget=True,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "max_budget": None,
        },
    )


@patch("httpx.AsyncClient")
def test_update_key_budget_clear_budget_duration_sends_null(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_key_budget(
            litellm_token="test-token",
            budget_duration=None,
            clear_budget_duration=True,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": None,
        },
    )


@patch("httpx.AsyncClient")
def test_update_key_budget_clear_budget_fields_send_nulls(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_key_budget(
            litellm_token="test-token",
            budget_duration=None,
            max_budget=None,
            clear_max_budget=True,
            clear_budget_duration=True,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": None,
            "max_budget": None,
        },
    )


@patch("httpx.AsyncClient")
def test_update_key_budget_can_toggle_blocked(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_key_budget(
            litellm_token="test-token",
            blocked=False,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "blocked": False,
        },
    )


@patch("httpx.AsyncClient")
def test_update_key_duration_success(
    mock_client_class, test_region, mock_httpx_post_client
):
    """Test successful key duration update"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_duration("test-token", "30d"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"key": "test-token", "duration": "30d"},
    )


@patch("httpx.AsyncClient")
def test_update_key_duration_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """Test key duration update failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_duration("test-token", "30d"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key duration" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_set_key_restrictions_success(
    mock_client_class, test_region, mock_httpx_post_client
):
    """Test successful key restrictions setting"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(
        service.set_key_restrictions(
            "test-token", "30d", 100.0, 1000, "monthly", spend=None
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "duration": "30d",
            "budget_duration": "monthly",
            "max_budget": 100.0,
            "rpm_limit": 1000,
        },
    )


@patch("httpx.AsyncClient")
def test_set_key_restrictions_success_with_spend(
    mock_client_class, test_region, mock_httpx_post_client
):
    """Test successful key restrictions setting with spend override"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.set_key_restrictions(
            "test-token", "30d", 100.0, 1000, "monthly", spend=0.0
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "duration": "30d",
            "budget_duration": "monthly",
            "max_budget": 100.0,
            "rpm_limit": 1000,
            "spend": 0.0,
        },
    )


@patch("httpx.AsyncClient")
def test_set_key_restrictions_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """Test key restrictions setting failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            service.set_key_restrictions("test-token", "30d", 100.0, 1000, "monthly")
        )

    assert exc_info.value.status_code == 500
    assert "Failed to set LiteLLM key restrictions" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_key_team_association_success(
    mock_client_class, test_region, mock_httpx_post_client
):
    """
    Given a LiteLLM service and valid token
    When updating key team association
    Then the request should succeed
    """
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_team_association("test-token", "new-team-id"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"key": "test-token", "team_id": "new-team-id"},
    )


@patch("httpx.AsyncClient")
def test_update_key_team_association_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """
    Given a LiteLLM service and invalid token
    When updating key team association
    Then an HTTPException should be raised
    """
    mock_client_class.return_value = mock_httpx_failure_client(404, "Key not found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_team_association("invalid-token", "new-team-id"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key team association" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_create_user_success(mock_client_class, test_region, mock_httpx_post_client):
    mock_client_class.return_value = mock_httpx_post_client
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(service.create_user(user_id="123", user_email="user@example.com"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/user/new",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "user_id": "123",
            "user_email": "user@example.com",
            "auto_create_key": False,
        },
    )


@patch("httpx.AsyncClient")
def test_create_user_idempotent_existing(mock_client_class, test_region):
    mock_response = Mock(status_code=409)
    mock_response.text = '{"error":"User already exists"}'
    mock_response.json.return_value = {"error": "User already exists"}
    mock_response.raise_for_status.side_effect = HTTPStatusError(
        "Conflict",
        request=httpx.Request("POST", "http://test/user/new"),
        response=mock_response,
    )
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    asyncio.run(service.create_user(user_id="123", user_email="user@example.com"))


@patch("httpx.AsyncClient")
def test_add_team_member_success(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(service.add_team_member(team_id="team-1", user_id="123", role="admin"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/team/member_add",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"team_id": "team-1", "member": {"user_id": "123", "role": "admin"}},
    )


@patch("httpx.AsyncClient")
def test_remove_team_member_success(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(service.remove_team_member(team_id="team-1", user_id="123"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/team/member_delete",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"team_id": "team-1", "user_id": "123"},
    )


@patch("httpx.AsyncClient")
def test_update_team_budget_includes_model_aliases(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_team_budget(
            team_id="team-1",
            max_budget=10.0,
            budget_duration="1mo",
            spend=0.0,
            model_aliases={"gpt-4": "azure/gpt-4-turbo-2024-04-09"},
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/team/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "team_id": "team-1",
            "max_budget": 10.0,
            "budget_duration": "1mo",
            "spend": 0.0,
            "model_aliases": {"gpt-4": "azure/gpt-4-turbo-2024-04-09"},
        },
    )


@patch("httpx.AsyncClient")
def test_update_team_budget_clear_budget_duration_sends_null(
    mock_client_class, test_region, mock_httpx_post_client
):
    mock_client_class.return_value = mock_httpx_post_client
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    asyncio.run(
        service.update_team_budget(
            team_id="team-1",
            max_budget=None,
            budget_duration=None,
            clear_budget_duration=True,
        )
    )

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/team/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "team_id": "team-1",
            "max_budget": None,
            "budget_duration": None,
        },
    )


def test_get_team_model_aliases_reads_team_info(test_region):
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    service.get_team_info = AsyncMock(
        return_value={
            "team_info": {
                "model_aliases": {
                    "gpt-4": "azure/gpt-4-turbo-2024-04-09",
                    "claude": "anthropic/claude-3-5-sonnet",
                }
            }
        }
    )

    aliases = asyncio.run(service.get_team_model_aliases("team-1"))

    assert aliases == {
        "gpt-4": "azure/gpt-4-turbo-2024-04-09",
        "claude": "anthropic/claude-3-5-sonnet",
    }


def test_get_team_model_aliases_reads_nested_litellm_model_table(test_region):
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    service.get_team_info = AsyncMock(
        return_value={
            "team_info": {
                "litellm_model_table": {
                    "model_aliases": {
                        "gpt-4": "azure/gpt-4-turbo-2024-04-09",
                    }
                }
            }
        }
    )

    aliases = asyncio.run(service.get_team_model_aliases("team-1"))

    assert aliases == {
        "gpt-4": "azure/gpt-4-turbo-2024-04-09",
    }


@patch("httpx.AsyncClient")
def test_get_team_model_aliases_falls_back_to_team_list(mock_client_class, test_region):
    """When team_info has no aliases, fallback to /team/list to find them."""
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    service.get_team_info = AsyncMock(return_value={"team_info": {}})

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = [
        {"team_id": "other-team", "litellm_model_table": {"model_aliases": {"x": "y"}}},
        {
            "team_id": "team-1",
            "litellm_model_table": {
                "model_aliases": {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}
            },
        },
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    aliases = asyncio.run(service.get_team_model_aliases("team-1"))

    assert aliases == {"gpt-4": "azure/gpt-4-turbo-2024-04-09"}


@patch("httpx.AsyncClient")
def test_get_team_model_aliases_empty_dict_means_no_aliases(
    mock_client_class, test_region
):
    """An empty model_aliases dict ({}) should be returned as-is, not treated as missing."""
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    service.get_team_info = AsyncMock(return_value={"team_info": {"model_aliases": {}}})

    aliases = asyncio.run(service.get_team_model_aliases("team-1"))

    assert aliases == {}
    # /team/list should NOT have been called — no httpx client instantiated
    mock_client_class.assert_not_called()


@patch("httpx.AsyncClient")
def test_get_team_model_aliases_team_list_fallback_returns_none_when_not_found(
    mock_client_class, test_region
):
    """Fallback /team/list returns empty aliases when team_id is not present."""
    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    service.get_team_info = AsyncMock(return_value={"team_info": {}})

    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = [
        {"team_id": "other-team", "litellm_model_table": {"model_aliases": {"x": "y"}}}
    ]

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    aliases = asyncio.run(service.get_team_model_aliases("team-1"))

    assert aliases == {}


def test_hash_token_hashes_sk_keys():
    """sk- keys are SHA-256 hashed the way LiteLLM stores them."""
    token = "sk-abc123"
    expected = hashlib.sha256(token.encode()).hexdigest()
    assert LiteLLMService.hash_token(token) == expected


def test_hash_token_passes_through_non_sk_tokens():
    """Tokens that are not sk- keys are returned unchanged."""
    assert LiteLLMService.hash_token("already-hashed-token") == "already-hashed-token"


@patch("httpx.AsyncClient")
def test_get_key_last_used_returns_latest_start_time(mock_client_class, test_region):
    """The most recent spend log startTime is returned as a datetime."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{"startTime": "2025-06-15T10:30:00.123000Z"}],
        "total": 42,
        "page": 1,
        "page_size": 1,
        "total_pages": 42,
    }
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.get_key_last_used("sk-abc123"))

    assert result == datetime(2025, 6, 15, 10, 30, 0, 123000, tzinfo=timezone.utc)

    # Filters by the hashed token and requests only the single latest row.
    call = mock_client.get.call_args
    assert call.args[0] == f"{test_region.litellm_api_url}/spend/logs/v2"
    params = call.kwargs["params"]
    assert params["api_key"] == hashlib.sha256(b"sk-abc123").hexdigest()
    assert params["page_size"] == 1
    assert params["start_date"] == "1970-01-01 00:00:00"


@patch("httpx.AsyncClient")
def test_get_key_last_used_returns_none_when_never_used(mock_client_class, test_region):
    """A key with no spend logs yields None."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [],
        "total": 0,
        "page": 1,
        "page_size": 1,
        "total_pages": 0,
    }
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.get_key_last_used("sk-abc123"))

    assert result is None


@patch("httpx.AsyncClient")
def test_get_key_last_used_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """A LiteLLM error is surfaced as an HTTPException."""
    mock_client_class.return_value = mock_httpx_failure_client(
        500, "Internal Server Error"
    )

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.get_key_last_used("sk-abc123"))

    assert "Failed to get LiteLLM key last-used time" in exc_info.value.detail


def _daily_activity_page(results, has_more):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": results,
        "metadata": {"has_more": has_more},
    }
    mock_response.raise_for_status.return_value = None
    return mock_response


@patch("httpx.AsyncClient")
def test_get_daily_activity_paginates_and_filters_by_hashed_key(
    mock_client_class, test_region
):
    """get_daily_activity follows pagination and filters by the hashed token."""
    page1 = _daily_activity_page(
        [{"date": "2025-06-01", "metrics": {"spend": 1.0}}], has_more=True
    )
    page2 = _daily_activity_page(
        [{"date": "2025-06-02", "metrics": {"spend": 2.0}}], has_more=False
    )

    mock_client = AsyncMock()
    mock_client.get.side_effect = [page1, page2]
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client_class.return_value = mock_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    results = asyncio.run(
        service.get_daily_activity(
            litellm_token="sk-abc123",
            start_date="2025-06-01",
            end_date="2025-06-02",
        )
    )

    assert [r["date"] for r in results] == ["2025-06-01", "2025-06-02"]
    assert mock_client.get.call_count == 2

    import hashlib

    expected_hash = hashlib.sha256(b"sk-abc123").hexdigest()
    first_call = mock_client.get.call_args_list[0]
    assert first_call.kwargs["params"]["api_key"] == expected_hash
    assert first_call.kwargs["params"]["start_date"] == "2025-06-01"
    assert first_call.kwargs["params"]["end_date"] == "2025-06-02"
    assert first_call.kwargs["params"]["page"] == 1
    # Second page requested when has_more is True.
    assert mock_client.get.call_args_list[1].kwargs["params"]["page"] == 2


@patch("httpx.AsyncClient")
def test_get_daily_activity_failure(
    mock_client_class, test_region, mock_httpx_failure_client
):
    """A LiteLLM error is surfaced as an HTTPException."""
    mock_client_class.return_value = mock_httpx_failure_client(
        500, "Internal Server Error"
    )

    service = LiteLLMService(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            service.get_daily_activity(
                litellm_token="sk-abc123",
                start_date="2025-06-01",
                end_date="2025-06-02",
            )
        )

    assert "Failed to get LiteLLM daily activity" in exc_info.value.detail
