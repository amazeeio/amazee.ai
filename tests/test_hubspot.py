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


def _make_async_client(*responses) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post = AsyncMock(side_effect=list(responses))
    client.patch = AsyncMock()
    client.put = AsyncMock()
    return client


@pytest.fixture
def service() -> HubSpotService:
    svc = HubSpotService(token="test-token")
    svc.marketing_subscription_id = "1110685904"
    return svc


@pytest.mark.asyncio
async def test_upsert_existing_contact_updates_property_and_subscription(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search
    )
    mock_client.patch.return_value = _make_response(200, {})
    mock_client.put.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates("user@example.com", enabled=True)

    assert mock_client.post.call_count == 1
    assert (
        "/crm/v3/objects/contacts/search" in mock_client.post.call_args_list[0].args[0]
    )
    assert mock_client.patch.call_count == 1
    assert "/crm/v3/objects/contacts/42" in mock_client.patch.call_args.args[0]
    assert mock_client.patch.call_args.kwargs["json"] == {
        "properties": {"receive_marketing_updates": "true"}
    }
    assert mock_client.put.call_count == 1
    assert (
        "/email/public/v1/subscriptions/user@example.com"
        in mock_client.put.call_args.args[0]
    )
    assert mock_client.put.call_args.kwargs["json"] == {
        "subscriptionId": "1110685904",
        "subscribed": True,
    }


@pytest.mark.asyncio
async def test_upsert_new_contact_creates_then_updates(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": []}),  # search none
        _make_response(201, {"id": "77"}),  # create
    )
    mock_client.patch.return_value = _make_response(200, {})
    mock_client.put.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates(
            "user@example.com", enabled=False
        )

    assert mock_client.post.call_count == 2
    assert (
        "/crm/v3/objects/contacts/search" in mock_client.post.call_args_list[0].args[0]
    )
    assert "/crm/v3/objects/contacts" in mock_client.post.call_args_list[1].args[0]
    assert mock_client.post.call_args_list[1].kwargs["json"] == {
        "properties": {"email": "user@example.com"}
    }
    assert "/crm/v3/objects/contacts/77" in mock_client.patch.call_args.args[0]
    assert mock_client.patch.call_args.kwargs["json"] == {
        "properties": {"receive_marketing_updates": "false"}
    }
    assert mock_client.put.call_args.kwargs["json"] == {
        "subscriptionId": "1110685904",
        "subscribed": False,
    }


@pytest.mark.asyncio
async def test_search_error_raises_502(service):
    mock_client = _make_async_client(_make_response(500, {"message": "server error"}))
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_property_update_error_raises_502(service):
    mock_client = _make_async_client(_make_response(200, {"results": [{"id": "42"}]}))
    mock_client.patch.return_value = _make_response(400, {"message": "bad request"})
    mock_client.put.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_subscription_update_error_raises_502(service):
    mock_client = _make_async_client(_make_response(200, {"results": [{"id": "42"}]}))
    mock_client.patch.return_value = _make_response(200, {})
    mock_client.put.return_value = _make_response(400, {"message": "bad request"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_missing_subscription_id_raises_404_and_skips_request(service):
    mock_client = _make_async_client(_make_response(200, {"results": [{"id": "42"}]}))
    mock_client.patch.return_value = _make_response(200, {})

    service.marketing_subscription_id = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )

    assert exc_info.value.status_code == 404
    assert mock_client.put.call_count == 0
