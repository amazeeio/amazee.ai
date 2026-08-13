"""Spend tracking and budget-cap enforcement against a real LiteLLM proxy.

The manual-test checklist devs run before a LiteLLM bump: spend accrues and is
visible through the backend's spend endpoints, and caps at key/team level
actually stop requests. All assertions poll (spend flushes asynchronously) and
are behavioral — never exact-float, token counts may drift across versions.
"""

import pytest

from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
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


@pytest.mark.asyncio
async def test_spend_visible_via_backend_endpoints(
    client, admin_token, litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    assert completion(LITELLM_A_URL, key["litellm_token"]).status_code == 200
    await wait_for_key_spend(LITELLM_A_URL, key["litellm_token"])

    def key_spend_via_backend():
        resp = client.get(
            f"/spend/{litellm_region.id}/key/{key['id']}",
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        return resp.json() if resp.json()["spend"] > 0 else None

    wait_for(key_spend_via_backend, message="key spend via GET /spend/.../key")

    resp = client.get(
        f"/spend/{litellm_region.id}/team/{team['id']}",
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_spend"] > 0


@pytest.mark.asyncio
async def test_backend_key_budget_cap_blocks_then_raise_unblocks(
    client, admin_token, litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    assert completion(LITELLM_A_URL, token).status_code == 200
    cost_per_call = await wait_for_key_spend(LITELLM_A_URL, token)

    # Cap just above current spend via the backend endpoint (which also
    # derives budget_duration server-side), then burn past it.
    resp = client.put(
        f"/spend/{litellm_region.id}/key/{key['id']}/budget",
        json={"max_budget": cost_per_call * 1.5},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text

    rejected = wait_for(
        lambda: _blocked(token), message="key cap to block requests"
    )
    assert "budget" in rejected.text.lower()

    # Raising the cap through the backend must unblock the key.
    resp = client.put(
        f"/spend/{litellm_region.id}/key/{key['id']}/budget",
        json={"max_budget": cost_per_call * 1000},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200, resp.text
    wait_for(lambda: _allowed(token), message="key to unblock after raise")


@pytest.mark.asyncio
async def test_team_budget_cap_blocks_all_team_keys(
    litellm_region, make_team, make_key
):
    team = make_team()
    key1 = make_key(team_id=team["id"], region_id=litellm_region.id)
    key2 = make_key(team_id=team["id"], region_id=litellm_region.id)

    assert completion(LITELLM_A_URL, key1["litellm_token"]).status_code == 200
    cost = await wait_for_key_spend(LITELLM_A_URL, key1["litellm_token"])

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    lt_team_id = LiteLLMService.format_team_id(litellm_region.name, team["id"])
    await service.update_team_budget(lt_team_id, max_budget=cost * 1.2)

    # Burn key1 past the team cap; key2 never spent, but the team-level cap
    # must reject it too.
    wait_for(
        lambda: _blocked(key1["litellm_token"]),
        message="team cap to block key1",
    )
    rejected = wait_for(
        lambda: _blocked(key2["litellm_token"]),
        message="team cap to block sibling key2",
    )
    assert "budget" in rejected.text.lower()


@pytest.mark.asyncio
async def test_blocked_flag_rejects_and_unblock_restores(
    litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)

    assert completion(LITELLM_A_URL, token).status_code == 200

    await service.update_key_budget(token, blocked=True)
    wait_for(lambda: _blocked(token), message="blocked key to be rejected")

    await service.update_key_budget(token, blocked=False)
    wait_for(lambda: _allowed(token), message="unblocked key to work again")


@pytest.mark.asyncio
async def test_rpm_limit_enforced(litellm_region, make_team, make_key):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)

    await service.set_key_restrictions(
        token, duration="30d", budget_amount=1000.0, rpm_limit=1
    )

    # One call may pass per minute; hammering must hit a 429 well within the
    # window regardless of where the minute boundary falls.
    resp = wait_for(
        lambda: _blocked(token),
        timeout=45,
        interval=0.2,
        message="rpm limit to reject requests",
    )
    assert resp.status_code == 429, resp.text
