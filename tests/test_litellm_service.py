import pytest
import asyncio
import requests
from unittest.mock import patch
from fastapi import HTTPException
from app.services.litellm import LiteLLMService
from requests.exceptions import HTTPError


@pytest.fixture
def mock_litellm_response():
    return {"key": "test-private-key-123"}


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


@patch("app.services.litellm.requests.post")
def test_create_key_success(mock_post, test_region, mock_litellm_response):
    """Test successful key creation"""
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = mock_litellm_response
    mock_post.return_value.raise_for_status.return_value = None

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
    mock_post.assert_called_once()


@patch("app.services.litellm.requests.post")
def test_create_key_failure(mock_post, test_region):
    """Test key creation failure"""
    mock_post.return_value.status_code = 500
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Internal Server Error")

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


@patch("app.services.litellm.requests.post")
def test_delete_key_success(mock_post, test_region):
    """Test successful key deletion"""
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("test-token"))

    assert result is True
    mock_post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/delete",
        json={"keys": ["test-token"]},
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"}
    )


@patch("app.services.litellm.requests.post")
def test_delete_key_not_found(mock_post, test_region):
    """Test key deletion when key not found (should return True)"""
    mock_post.return_value.status_code = 404

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.delete_key("non-existent-token"))

    assert result is True


@patch("app.services.litellm.requests.post")
def test_delete_key_failure(mock_post, test_region):
    """Test key deletion failure"""
    mock_post.return_value.status_code = 500
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Internal Server Error")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.delete_key("test-token"))

    assert exc_info.value.status_code == 500
    assert "Failed to delete LiteLLM key" in exc_info.value.detail


@patch("app.services.litellm.requests.get")
def test_get_key_info_success(mock_get, test_region):
    """Test successful key info retrieval"""
    mock_response = {
        "info": {
            "key_name": "Test Key",
            "spend": 10.5,
            "expires": "2024-12-31T23:59:59Z"
        }
    }
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = mock_response
    mock_get.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    result = asyncio.run(service.get_key_info("test-token"))

    assert result == mock_response
    mock_get.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/info",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        params={"key": "test-token"}
    )


@patch("app.services.litellm.requests.get")
def test_get_key_info_failure(mock_get, test_region):
    """Test key info retrieval failure"""
    mock_get.return_value.status_code = 404
    mock_get.return_value.raise_for_status.side_effect = HTTPError("Not Found")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.get_key_info("test-token"))

    assert exc_info.value.status_code == 500
    assert "Failed to get LiteLLM key information" in exc_info.value.detail


@patch("app.services.litellm.requests.post")
def test_update_budget_success(mock_post, test_region):
    """Test successful budget update"""
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_budget("test-token", "monthly", 100.0))

    mock_post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "budget_duration": "monthly",
            "duration": "365d",
            "max_budget": 100.0
        }
    )


@patch("app.services.litellm.requests.post")
def test_update_budget_failure(mock_post, test_region):
    """Test budget update failure"""
    mock_post.return_value.status_code = 400
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_budget("test-token", "monthly", 100.0))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM budget" in exc_info.value.detail


@patch("app.services.litellm.requests.post")
def test_update_key_duration_success(mock_post, test_region):
    """Test successful key duration update"""
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_duration("test-token", "30d"))

    mock_post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": "test-token",
            "duration": "30d"
        }
    )


@patch("app.services.litellm.requests.post")
def test_update_key_duration_failure(mock_post, test_region):
    """Test key duration update failure"""
    mock_post.return_value.status_code = 400
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Bad Request")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_duration("test-token", "30d"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key duration" in exc_info.value.detail


@patch("app.services.litellm.requests.post")
def test_set_key_restrictions_success(mock_post, test_region):
    """Test successful key restrictions setting"""
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.set_key_restrictions(
        "test-token", "30d", 100.0, 1000, "monthly"
    ))

    mock_post.assert_called_once_with(
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


@patch("app.services.litellm.requests.post")
def test_set_key_restrictions_failure(mock_post, test_region):
    """Test key restrictions setting failure"""
    mock_post.return_value.status_code = 400
    mock_post.return_value.raise_for_status.side_effect = HTTPError("Bad Request")

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


@patch("app.services.litellm.requests.post")
def test_update_key_team_association_success(mock_post, test_region):
    """
    Given a LiteLLM service and valid token
    When updating key team association
    Then the request should succeed
    """
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # This should not raise an exception
    asyncio.run(service.update_key_team_association("test-token", "new-team-id"))

    mock_post.assert_called_once_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={"key": "test-token", "team_id": "new-team-id"}
    )


@patch("app.services.litellm.requests.post")
def test_update_key_team_association_failure(mock_post, test_region):
    """
    Given a LiteLLM service and invalid token
    When updating key team association
    Then an HTTPException should be raised
    """
    mock_post.return_value.status_code = 404
    mock_post.return_value.json.return_value = {"error": "Key not found"}
    mock_post.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError("404")

    service = LiteLLMService(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(service.update_key_team_association("invalid-token", "new-team-id"))

    assert exc_info.value.status_code == 500
    assert "Failed to update LiteLLM key team association" in exc_info.value.detail
