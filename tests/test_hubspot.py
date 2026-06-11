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
# Subscribe (enabled=True) — existing contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_existing_contact_subscribe(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search
        _make_response(200, {"id": "1110685904", "status": "SUBSCRIBED"}),  # subscribe
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketing_updates("user@example.com", enabled=True)

    assert mock_client.post.call_count == 2
    # call 0 — contact search
    assert (
        "/crm/v3/objects/contacts/search" in mock_client.post.call_args_list[0].args[0]
    )
    # call 1 — v3 subscribe
    assert (
        "/communication-preferences/v3/subscribe"
        in mock_client.post.call_args_list[1].args[0]
    )
    assert mock_client.post.call_args_list[1].kwargs["json"] == {
        "emailAddress": "user@example.com",
        "subscriptionId": "1110685904",
        "legalBasis": "LEGITIMATE_INTEREST_PQL",
        "legalBasisExplanation": "User preference updated via amazee.ai platform",
    }
    # contact property update
    assert mock_client.patch.call_count == 1
    assert "/crm/v3/objects/contacts/42" in mock_client.patch.call_args.args[0]
    assert mock_client.patch.call_args.kwargs["json"] == {
        "properties": {"receive_marketing_updates": "true"}
    }


# ---------------------------------------------------------------------------
# Unsubscribe (enabled=False) — existing contact
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


# ---------------------------------------------------------------------------
# New contact — create then subscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_new_contact_creates_then_subscribes(service):
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
# HubSpot blocks re-subscription (previously opted out) — warning, no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resubscribe_blocked_by_hubspot_logs_warning_and_continues(service):
    """HubSpot returns 400 VALIDATION_ERROR when re-subscribing an opted-out contact.
    The service must log a warning and NOT raise — local DB update still proceeds.
    """
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),  # search
        _make_response(
            400,
            {
                "status": "error",
                "message": "Subscription 1110685904 for user@example.com cannot be updated because they have unsubscribed",
                "category": "VALIDATION_ERROR",
            },
        ),  # subscribe blocked
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Should NOT raise
        await service.upsert_contact_marketing_updates("user@example.com", enabled=True)

    # Contact property still updated despite blocked subscription
    assert mock_client.patch.call_count == 1


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
        _make_response(200, {"id": "1110685904", "status": "SUBSCRIBED"}),
    )
    mock_client.patch.return_value = _make_response(400, {"message": "bad request"})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_subscription_non_validation_error_raises_502(service):
    """A non-VALIDATION_ERROR 400 (or 5xx) from HubSpot must still raise 502."""
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "42"}]}),
        _make_response(500, {"message": "internal server error"}),
    )
    mock_client.patch.return_value = _make_response(200, {})

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_missing_subscription_id_raises_404(service):
    mock_client = _make_async_client(_make_response(200, {"results": [{"id": "42"}]}))
    mock_client.patch.return_value = _make_response(200, {})
    service.marketing_subscription_id = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketing_updates(
                "user@example.com", enabled=True
            )

    assert exc_info.value.status_code == 404
    # subscribe/unsubscribe POST was never called (only search POST was)
    assert mock_client.post.call_count == 1
