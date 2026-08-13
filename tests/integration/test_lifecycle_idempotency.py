"""Entity lifecycle round-trips and idempotency against a real LiteLLM proxy.

The idempotency tests are the classic bump canary: _is_idempotent_litellm_error
matches real status codes and error strings from LiteLLM's duplicate-create /
already-deleted responses, and those strings change between versions.
"""

from uuid import uuid4

import pytest

from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    _auth,
    completion,
    wait_for,
)


@pytest.mark.asyncio
async def test_team_bootstrap_visible_on_proxy(litellm_region, make_team):
    team = make_team()
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    lt_team_id = LiteLLMService.format_team_id(litellm_region.name, team["id"])

    info = await service.get_team_info(lt_team_id)
    assert info.get("team_id", info.get("team_info", {}).get("team_id")) == (
        lt_team_id
    ), f"team missing on proxy after backend bootstrap: {info}"


@pytest.mark.asyncio
async def test_key_delete_removes_key_from_proxy(
    client, admin_token, litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]
    assert completion(LITELLM_A_URL, token).status_code == 200

    resp = client.delete(
        f"/private-ai-keys/{key['id']}", headers=_auth(admin_token)
    )
    assert resp.status_code == 200, resp.text

    def rejected_by_proxy():
        resp = completion(LITELLM_A_URL, token)
        return resp if resp.status_code >= 400 else None

    rejected = wait_for(
        rejected_by_proxy, message="deleted key to be rejected by the proxy"
    )
    assert rejected.status_code in (400, 401, 404), rejected.text


@pytest.mark.asyncio
async def test_user_and_membership_operations_are_idempotent(
    litellm_region, make_team
):
    """Every duplicated call must be swallowed by _is_idempotent_litellm_error.

    If a LiteLLM bump changes the duplicate/already-gone error strings or
    status codes, these second calls raise and the gate goes red — exactly
    the regression this suite exists to catch.
    """
    team = make_team()
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    lt_team_id = LiteLLMService.format_team_id(litellm_region.name, team["id"])

    user_id = f"int-user-{uuid4().hex[:8]}"
    email = f"{user_id}@example.com"

    await service.create_user(user_id=user_id, user_email=email)
    await service.create_user(user_id=user_id, user_email=email)  # duplicate

    await service.add_team_member(lt_team_id, user_id)
    await service.add_team_member(lt_team_id, user_id)  # duplicate

    await service.remove_team_member(lt_team_id, user_id)
    await service.remove_team_member(lt_team_id, user_id)  # already gone

    await service.delete_user(user_id)
    await service.delete_user(user_id)  # already gone
