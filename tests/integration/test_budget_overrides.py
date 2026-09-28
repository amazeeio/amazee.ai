"""Budget overrides and their clear paths through the backend spend API.

test_spend_and_caps.py pins that caps block. This pins the other half, the
parts LiteLLM bumps tend to break: team caps set via the backend endpoint (not
the service layer), per-member caps (`max_budget_in_team`), and clearing each
override again by sending nulls to LiteLLM.
"""

from uuid import uuid4

import pytest

from tests.integration.conftest import (
    LITELLM_A_URL,
    _auth,
    completion,
    wait_for,
    wait_for_key_spend,
)


def _blocked(token):
    resp = completion(LITELLM_A_URL, token)
    return resp if resp.status_code >= 400 else None


def _allowed(token):
    resp = completion(LITELLM_A_URL, token)
    return resp if resp.status_code == 200 else None


def _create_member(client, admin_token, team_id):
    resp = client.post(
        "/users",
        json={
            "email": f"int-member-{uuid4().hex[:8]}@example.com",
            "password": "integration-password-1",
            "team_id": team_id,
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, f"user creation failed: {resp.text}"
    return resp.json()


def _create_member_key(client, admin_token, region_id, owner_id):
    """A key owned by a team member, so LiteLLM tracks membership spend."""
    resp = client.post(
        "/private-ai-keys",
        json={
            "region_id": region_id,
            "owner_id": owner_id,
            "name": f"int-member-key-{uuid4().hex[:6]}",
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, f"key creation failed: {resp.text}"
    return resp.json()


@pytest.mark.asyncio
async def test_backend_key_budget_clear_removes_cap(
    client, admin_token, litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]
    base = f"/spend/{litellm_region.id}/key/{key['id']}/budget"

    assert completion(LITELLM_A_URL, token).status_code == 200
    cost = await wait_for_key_spend(LITELLM_A_URL, token)

    resp = client.put(base, json={"max_budget": cost * 1.5}, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    wait_for(lambda: _blocked(token), message="key cap to block requests")

    resp = client.post(f"{base}/clear", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Clearing sends explicit nulls; a LiteLLM that ignores them leaves the
    # old cap in place and the key blocked forever.
    assert body["max_budget"] is None
    assert body["budget_duration"] is None
    wait_for(lambda: _allowed(token), message="key to unblock after clear")


@pytest.mark.asyncio
async def test_backend_team_budget_blocks_then_clear_restores(
    client, admin_token, litellm_region, make_team, make_key
):
    # PERIODIC teams (the default) reject manual team budgets; a non-gated
    # POOL team is the path where an admin sets one directly.
    team = make_team(budget_type="pool")
    key1 = make_key(team_id=team["id"], region_id=litellm_region.id)
    key2 = make_key(team_id=team["id"], region_id=litellm_region.id)
    base = f"/spend/{litellm_region.id}/team/{team['id']}/budget"

    assert completion(LITELLM_A_URL, key1["litellm_token"]).status_code == 200
    cost = await wait_for_key_spend(LITELLM_A_URL, key1["litellm_token"])

    resp = client.put(base, json={"max_budget": cost * 1.2}, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    # budget_duration is read back from /team/info, so this also pins that
    # the proxy stores the duration the backend sends.
    assert resp.json()["budget_duration"] == "1mo"

    wait_for(lambda: _blocked(key1["litellm_token"]), message="team cap to block key1")
    rejected = wait_for(
        lambda: _blocked(key2["litellm_token"]), message="team cap to block key2"
    )
    assert "budget" in rejected.text.lower()

    resp = client.post(f"{base}/clear", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_budget"] > cost * 1.2
    wait_for(
        lambda: _allowed(key2["litellm_token"]),
        message="team keys to unblock after clear",
    )


@pytest.mark.asyncio
async def test_team_member_budget_caps_only_that_member(
    client, admin_token, litellm_region, make_team
):
    team = make_team()
    capped = _create_member(client, admin_token, team["id"])
    other = _create_member(client, admin_token, team["id"])
    capped_key = _create_member_key(
        client, admin_token, litellm_region.id, capped["id"]
    )
    other_key = _create_member_key(client, admin_token, litellm_region.id, other["id"])
    token = capped_key["litellm_token"]
    base = f"/spend/{litellm_region.id}/team/{team['id']}/member/{capped['id']}/budget"

    assert completion(LITELLM_A_URL, token).status_code == 200
    cost = await wait_for_key_spend(LITELLM_A_URL, token)

    # The backend pushes max_budget_in_team = current member spend + cap, so
    # this leaves roughly half a call of headroom.
    resp = client.put(base, json={"max_budget": cost * 0.5}, headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text

    rejected = wait_for(lambda: _blocked(token), message="member cap to block member")
    assert "budget" in rejected.text.lower()
    # The cap is per-member: a teammate's key keeps working.
    assert completion(LITELLM_A_URL, other_key["litellm_token"]).status_code == 200

    resp = client.post(f"{base}/clear", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    wait_for(lambda: _allowed(token), message="member to unblock after clear")
