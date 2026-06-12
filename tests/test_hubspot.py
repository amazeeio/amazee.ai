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
    """Build a mock httpx.AsyncClient where .post() cycles through post_responses."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post = AsyncMock(side_effect=list(post_responses))
    client.patch = AsyncMock()
    return client


@pytest.fixture
def service() -> HubSpotService:
    svc = HubSpotService(token="test-token")
    svc.marketing_subscription_id = "1110685904"
    return svc


# ---------------------------------------------------------------------------
# enabled=True — NO subscription API call (HubSpot defaults to SUBSCRIBED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_existing_contact_subscribe(service):
    """enabled=True: only search + property update. No subscribe POST call."""
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates("user@example.com", enabled=True)

    # Only 1 POST — the contact search. No subscribe call.
    assert mock_client.post.call_count == 1
    assert (
        "/crm/v3/objects/contacts/search" in mock_client.post.call_args_list[0].args[0]
    )
    assert mock_client.patch.call_count == 1
    assert "/crm/v3/objects/contacts/42" in mock_client.patch.call_args.args[0]
    assert mock_client.patch.call_args.kwargs["json"] == {
        "properties": {"receive_marketing_updates": "true"}
    }


@pytest.mark.asyncio
async def test_subscribe_skips_hubspot_subscription_call(service):
    """enabled=True never calls /v3/subscribe — HubSpot defaults to SUBSCRIBED
    and blocks programmatic re-subscription anyway.
    """
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search only
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates("user@example.com", enabled=True)

    # Only search POST — no subscribe POST
    assert mock_client.post.call_count == 1
    assert mock_client.patch.call_count == 1


# ---------------------------------------------------------------------------
# enabled=False — unsubscribe IS called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_existing_contact_unsubscribe(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search
        _make_response(
            200, {"id": "1110685904", "status": "NOT_SUBSCRIBED"}
        ),  # unsubscribe
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates(
            "user@example.com", enabled=False
        )

    assert mock_client.post.call_count == 2
    assert (
        "/communication-preferences/v3/unsubscribe"
        in mock_client.post.call_args_list[1].args[0]
    )
    assert (
        mock_client.post.call_args_list[1].kwargs["json"]["emailAddress"]
        == "user@example.com"
    )
    assert mock_client.patch.call_args.kwargs["json"] == {
        "properties": {"receive_marketing_updates": "false"}
    }


@pytest.mark.asyncio
async def test_upsert_new_contact_creates_then_unsubscribes(service):
    """enabled=False on new contact: search → create → property update → unsubscribe."""
    mock_client = _make_async_client(
        _make_response(200, {"results": []}),  # search → not found
        _make_response(201, {"id": "77"}),  # create contact
        _make_response(
            200, {"id": "1110685904", "status": "NOT_SUBSCRIBED"}
        ),  # unsubscribe
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates("new@example.com", enabled=False)

    assert mock_client.post.call_count == 3
    assert (
        "/crm/v3/objects/contacts/search" in mock_client.post.call_args_list[0].args[0]
    )
    assert "/crm/v3/objects/contacts" in mock_client.post.call_args_list[1].args[0]
    assert mock_client.post.call_args_list[1].kwargs["json"] == {
        "properties": {"email": "new@example.com"}
    }
    assert (
        "/communication-preferences/v3/unsubscribe"
        in mock_client.post.call_args_list[2].args[0]
    )
    assert "/crm/v3/objects/contacts/77" in mock_client.patch.call_args.args[0]


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


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
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),
    )
    mock_client.patch.return_value = _make_response(400, {"message": "bad request"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_unsubscribe_server_error_raises_502(service):
    """A 5xx from HubSpot on unsubscribe must raise 502."""
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),
        _make_response(500, {"message": "internal server error"}),
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=False
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_missing_subscription_id_raises_404_only_on_unsubscribe(service):
    """Missing subscription ID only matters when enabled=False (unsubscribe path)."""
    mock_client = _make_async_client(_make_response(200, {"results": [{"id": "42"}]}))
    mock_client.patch.return_value = _make_response(200, {})
    service.marketing_subscription_id = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=False
            )

    assert exc_info.value.status_code == 404
    # only search POST was called, no unsubscribe POST
    assert mock_client.post.call_count == 1
