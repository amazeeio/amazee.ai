import pytest
import asyncio
from unittest.mock import patch, AsyncMock, Mock
from fastapi import HTTPException
from app.services.litellm import LiteLLMService
from httpx import HTTPStatusError

@pytest.fixture
def mock_litellm_response():
    return {"key": "test-private-key-123"}


@pytest.fixture
def mock_httpx_failure_client():
    """Mock httpx.AsyncClient for operations that should fail"""
    def _create_failure_client(status_code, error_message):
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = HTTPStatusError(error_message, request=None, response=None)

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
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
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
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.create_key(
        email="test@example.com",
        name="Test Key",
        user_id=123,
        team_id="team-456"
    ))

    assert result == "test-private-key-123"
    mock_httpx_post_client.post.assert_called_once()


@patch("httpx.AsyncClient")
def test_create_key_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key creation failure"""
    mock_client_class.return_value = mock_httpx_failure_client(500, "Internal Server Error")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.create_key(
            email="test@example.com",
            name="Test Key",
            user_id=123,
            team_id="team-456"
        ))

    assert exc_info.value.status_code == 500
    assert "Failed to create LiteLLM key" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_delete_key_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful key deletion"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("test-token"))

    assert result is True
    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/delete",
        json={"keys": ["test-token"]},
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"}
    )


@patch("httpx.AsyncClient")
def test_delete_key_not_found(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key deletion when key not found (should return True)"""
    mock_client_class.return_value = mock_httpx_failure_client(404, "Not Found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("non-existent-token"))

    assert result is True


@patch("httpx.AsyncClient")
def test_delete_key_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key deletion failure"""
    mock_client_class.return_value = mock_httpx_failure_client(500, "Internal Server Error")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
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
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
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
            "budget_reset_at": "2024-02-01T00:00:00Z"
        }
    }
    assert result == expected_response
    mock_httpx_get_client.get.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/info",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        params={"key": "test-token"}
    )


@patch("httpx.AsyncClient")
def test_get_key_info_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key info retrieval failure"""
    mock_client_class.return_value = mock_httpx_failure_client(404, "Not Found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.get_key_info("test-token"))

    assert exc_info.value.status_code == 500
    assert "Failed to get LiteLLM key information" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_budget_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful budget update"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
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
            "max_budget": 100.0
        }
    )


@patch("httpx.AsyncClient")
def test_update_budget_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test budget update failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_budget("test-token", "monthly", 100.0))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM budget" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_key_duration_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful key duration update"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_duration("test-token", "30d"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "duration": "30d"
        }
    )


@patch("httpx.AsyncClient")
def test_update_key_duration_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key duration update failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_duration("test-token", "30d"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key duration" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_set_key_restrictions_success(mock_client_class, test_region, mock_httpx_post_client):
    """Test successful key restrictions setting"""
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.set_key_restrictions(
        "test-token", "30d", 100.0, 1000, "monthly"
    ))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "duration": "30d",
            "budget_duration": "monthly",
            "max_budget": 100.0,
            "rpm_limit": 1000
        }
    )


@patch("httpx.AsyncClient")
def test_set_key_restrictions_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """Test key restrictions setting failure"""
    mock_client_class.return_value = mock_httpx_failure_client(400, "Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.set_key_restrictions(
            "test-token", "30d", 100.0, 1000, "monthly"
        ))

    assert exc_info.value.status_code == 500
    assert "Failed to set LiteLLM key restrictions" in exc_info.value.detail


@patch("httpx.AsyncClient")
def test_update_key_team_association_success(mock_client_class, test_region, mock_httpx_post_client):
    """
    Given a LiteLLM service and valid token
    When updating key team association
    Then the request should succeed
    """
    mock_client_class.return_value = mock_httpx_post_client

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_team_association("test-token", "new-team-id"))

    mock_httpx_post_client.post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"key": "test-token", "team_id": "new-team-id"}
    )


@patch("httpx.AsyncClient")
def test_update_key_team_association_failure(mock_client_class, test_region, mock_httpx_failure_client):
    """
    Given a LiteLLM service and invalid token
    When updating key team association
    Then an HTTPException should be raised
    """
    mock_client_class.return_value = mock_httpx_failure_client(404, "Key not found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_team_association("invalid-token", "new-team-id"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key team association" in exc_info.value.detail
