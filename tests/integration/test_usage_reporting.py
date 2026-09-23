"""Usage-reporting reads against a real LiteLLM proxy.

Unit tests mock these LiteLLM responses, so a shape change in a bump
(/user/daily/activity, /team/daily/activity, /spend/logs/v2) goes unnoticed
until dashboards, budget alerts and subscription cycles read zeros. Each test
makes one real call and asserts it shows up through the backend.

Daily-activity tables and spend logs flush asynchronously, so every read
polls. Assertions are behavioral (spend > 0, request counted), never exact.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    _auth,
    completion,
    wait_for,
    wait_for_key_spend,
)


@pytest_asyncio.fixture
async def used_member_key(client, admin_token, litellm_region, make_team):
    """A team member's key that has made one real completion."""
    team = make_team()
    resp = client.post(
        "/users",
        json={
            "email": f"int-usage-{uuid4().hex[:8]}@example.com",
            "password": "integration-password-1",
            "team_id": team["id"],
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()
    resp = client.post(
        "/private-ai-keys",
        json={
            "region_id": litellm_region.id,
            "owner_id": user["id"],
            "name": f"int-usage-key-{uuid4().hex[:6]}",
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    key = resp.json()
    assert completion(LITELLM_A_URL, key["litellm_token"]).status_code == 200
    await wait_for_key_spend(LITELLM_A_URL, key["litellm_token"])
    return team, user, key


def _poll_activity(client, admin_token, path):
    """Poll a backend daily-activity endpoint until today's row has spend."""

    def read():
        resp = client.get(path, headers=_auth(admin_token))
        assert resp.status_code == 200, resp.text
        rows = [r for r in resp.json()["activity"] if r["spend"] > 0]
        return rows or None

    rows = wait_for(read, timeout=60, message=f"daily activity at {path}")
    assert sum(r["request_count"] for r in rows) >= 1
    assert sum(r["total_tokens"] for r in rows) > 0
    return rows


@pytest.mark.asyncio
async def test_key_daily_activity(client, admin_token, litellm_region, used_member_key):
    _, _, key = used_member_key
    _poll_activity(
        client,
        admin_token,
        f"/spend/{litellm_region.id}/key/{key['id']}/daily-activity",
    )


@pytest.mark.asyncio
async def test_user_daily_activity(
    client, admin_token, litellm_region, used_member_key
):
    _, user, _ = used_member_key
    _poll_activity(
        client,
        admin_token,
        f"/spend/{litellm_region.id}/user/{user['id']}/daily-activity",
    )


@pytest.mark.asyncio
async def test_team_daily_activity(
    client, admin_token, litellm_region, used_member_key
):
    team, _, _ = used_member_key
    _poll_activity(
        client,
        admin_token,
        f"/spend/{litellm_region.id}/team/{team['id']}/daily-activity",
    )


@pytest.mark.asyncio
async def test_team_breakdown_attributes_usage_to_member_and_key(
    client, admin_token, litellm_region, used_member_key
):
    team, user, key = used_member_key
    path = f"/spend/{litellm_region.id}/team/{team['id']}/breakdown"

    def read():
        resp = client.get(path, headers=_auth(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body if body["totals"]["spend"] > 0 else None

    body = wait_for(read, timeout=60, message="team breakdown totals")
    member = next((u for u in body["users"] if u["user_id"] == user["id"]), None)
    assert member is not None, body
    assert any(k["key_id"] == key["id"] for k in member["keys"]), member


@pytest.mark.asyncio
async def test_key_last_used(client, admin_token, litellm_region, used_member_key):
    _, _, key = used_member_key
    path = f"/spend/{litellm_region.id}/key/{key['id']}/last-used"

    def read():
        resp = client.get(path, headers=_auth(admin_token))
        assert resp.status_code == 200, resp.text
        return resp.json()["last_used_at"]

    last_used = datetime.fromisoformat(
        wait_for(read, message="key last_used_at from spend logs").replace(
            "Z", "+00:00"
        )
    )
    if last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    assert datetime.now(UTC) - last_used < timedelta(minutes=10)


@pytest.mark.asyncio
async def test_team_spend_in_range_reads_spend_logs(litellm_region, used_member_key):
    """Subscription cycles and the worker bill from /spend/logs/v2 totals."""
    team, _, _ = used_member_key
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    lt_team_id = LiteLLMService.format_team_id(litellm_region.name, team["id"])
    now = datetime.now(UTC)

    async def spend():
        return await service.get_team_spend_in_range(
            lt_team_id, now - timedelta(hours=1), now + timedelta(hours=1)
        )

    # wait_for is sync; poll the async read by hand.
    for _ in range(60):
        if await spend() > 0:
            return
        await asyncio.sleep(1)
    raise AssertionError("team spend logs stayed at zero for 60s")
