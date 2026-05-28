import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.services.hubspot import HubSpotService


def _make_response(status_code: int, body: dict) -> MagicMock:
    """Return a mock httpx.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps(body)
    mock_resp.headers = {}
    mock_resp.json.return_value = body
    return mock_resp


def _make_async_client(*post_responses) -> AsyncMock:
    """Return a mock httpx.AsyncClient that yields *post_responses* in order."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.post = AsyncMock(side_effect=list(post_responses))
    return client


@pytest.fixture
def service() -> HubSpotService:
    return HubSpotService(token="test-token")


# ---------------------------------------------------------------------------
# _get_contact_vid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_contact_vid_found(service):
    client = _make_async_client(
        _make_response(200, {"results": [{"id": "42", "properties": {}}]})
    )
    vid = await service._get_contact_vid("a@b.com", client)
    assert vid == 42


@pytest.mark.asyncio
async def test_get_contact_vid_not_found_returns_none(service):
    client = _make_async_client(_make_response(200, {"results": []}))
    vid = await service._get_contact_vid("ghost@b.com", client)
    assert vid is None


@pytest.mark.asyncio
async def test_get_contact_vid_search_error_returns_none(service):
    client = _make_async_client(_make_response(500, {"message": "server error"}))
    vid = await service._get_contact_vid("a@b.com", client)
    assert vid is None


# ---------------------------------------------------------------------------
# upsert_contact_marketable_status — opt-in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opt_in_calls_marketing_contacts_add_endpoint(service):
    mock_client = _make_async_client(
        # search → found vid 7
        _make_response(200, {"results": [{"id": "7", "properties": {}}]}),
        # POST /marketingcontacts/v1/contacts → success
        _make_response(200, {}),
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketable_status("user@example.com", enabled=True)

    calls = mock_client.post.call_args_list
    assert len(calls) == 2
    # First call: search
    assert "/crm/v3/objects/contacts/search" in calls[0].args[0]
    # Second call: marketing add
    assert "/marketingcontacts/v1/contacts" in calls[1].args[0]
    assert "removals" not in calls[1].args[0]
    payload = calls[1].kwargs["json"]
    assert payload == {"vidsByLastUpdated": [{"vid": 7}]}


# ---------------------------------------------------------------------------
# upsert_contact_marketable_status — opt-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opt_out_calls_marketing_contacts_removals_endpoint(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "99", "properties": {}}]}),
        _make_response(200, {}),
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketable_status(
            "user@example.com", enabled=False
        )

    calls = mock_client.post.call_args_list
    assert len(calls) == 2
    assert "/marketingcontacts/v1/contacts/removals" in calls[1].args[0]
    payload = calls[1].kwargs["json"]
    assert payload == {"vidsByLastUpdated": [{"vid": 99}]}


# ---------------------------------------------------------------------------
# Contact not found in HubSpot — skip silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_contact_is_skipped(service):
    """If a contact cannot be found in HubSpot, skip without calling marketing API."""
    mock_client = _make_async_client(
        _make_response(200, {"results": []})  # not found
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contact_marketable_status(
            "ghost@example.com", enabled=True
        )

    # Only the search call should have been made
    assert mock_client.post.call_count == 1


# ---------------------------------------------------------------------------
# Batch — mixed opt-in and opt-out
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_mixed_routes_to_correct_endpoints(service):
    mock_client = _make_async_client(
        # search for optin@example.com → vid 1
        _make_response(200, {"results": [{"id": "1", "properties": {}}]}),
        # search for optout@example.com → vid 2
        _make_response(200, {"results": [{"id": "2", "properties": {}}]}),
        # POST /marketingcontacts/v1/contacts (add)
        _make_response(200, {}),
        # POST /marketingcontacts/v1/contacts/removals (remove)
        _make_response(200, {}),
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        await service.upsert_contacts_marketable_status(
            [("optin@example.com", True), ("optout@example.com", False)]
        )

    calls = mock_client.post.call_args_list
    assert len(calls) == 4
    add_call = calls[2]
    remove_call = calls[3]
    assert "/marketingcontacts/v1/contacts" in add_call.args[0]
    assert "removals" not in add_call.args[0]
    assert add_call.kwargs["json"] == {"vidsByLastUpdated": [{"vid": 1}]}
    assert "/marketingcontacts/v1/contacts/removals" in remove_call.args[0]
    assert remove_call.kwargs["json"] == {"vidsByLastUpdated": [{"vid": 2}]}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_api_error_raises_502(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "5", "properties": {}}]}),
        _make_response(400, {"message": "bad request"}),
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketable_status(
                "user@example.com", enabled=True
            )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_remove_api_error_raises_502(service):
    mock_client = _make_async_client(
        _make_response(200, {"results": [{"id": "5", "properties": {}}]}),
        _make_response(500, {"message": "server error"}),
    )
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc_info:
            await service.upsert_contact_marketable_status(
                "user@example.com", enabled=False
            )
    assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# Empty list is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_contacts_list_is_noop(service):
    with patch("httpx.AsyncClient") as mock_cls:
        await service.upsert_contacts_marketable_status([])
    mock_cls.assert_not_called()
