import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.hubspot import HubSpotService


def _make_response(status_code: int, body: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps(body)
    mock_resp.headers = {}
    mock_resp.json.return_value = body
    return mock_resp


def _make_async_client(*post_responses) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post = AsyncMock(side_effect=list(post_responses))
    return client


@pytest.fixture
def service() -> HubSpotService:
    return HubSpotService(token="test-token")


@pytest.mark.asyncio
async def test_create_contact_opt_in_calls_crm_create_endpoint(service):
    mock_client = _make_async_client(_make_response(201, {"id": "123"}))
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.create_contact_with_marketable_status(
            "user@example.com", enabled=True
        )

    call = mock_client.post.call_args
    assert "/crm/v3/objects/contacts" in call.args[0]
    assert call.kwargs["json"] == {
        "properties": {
            "email": "user@example.com",
            "hs_marketable_status": "true",
        }
    }


@pytest.mark.asyncio
async def test_create_contact_opt_out_sets_false(service):
    mock_client = _make_async_client(_make_response(201, {"id": "123"}))
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.create_contact_with_marketable_status(
            "user@example.com", enabled=False
        )

    call = mock_client.post.call_args
    assert call.kwargs["json"] == {
        "properties": {
            "email": "user@example.com",
            "hs_marketable_status": "false",
        }
    }


@pytest.mark.asyncio
async def test_create_contact_409_is_noop(service):
    mock_client = _make_async_client(
        _make_response(409, {"message": "Contact already exists"})
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.create_contact_with_marketable_status(
            "user@example.com", enabled=True
        )

    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_create_contact_error_raises_502(service):
    mock_client = _make_async_client(_make_response(400, {"message": "bad request"}))
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.create_contact_with_marketable_status(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502
