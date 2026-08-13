"""User -> LiteLLM sync fan-out through the real backend flows.

Semantics under test (docs/design/LiteLLM_User_Association_Notes.md): a user
lands in their team's region on creation, membership follows add/remove, and
dedicated regions only get the team + its members on explicit association.
LiteLLM user ids are str(db_user.id); team ids are format_team_id(region, id).
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_B_URL,
    LITELLM_MASTER_KEY,
    _auth,
)


async def _team_member_ids(url: str, region_name: str, team_id: int) -> set:
    service = LiteLLMService(url, LITELLM_MASTER_KEY)
    info = await service.get_team_info(
        LiteLLMService.format_team_id(region_name, team_id)
    )
    team_info = info.get("team_info", info)
    return {
        m.get("user_id") for m in team_info.get("members_with_roles", [])
    }


def _create_user(client, admin_token, team_id: int) -> dict:
    resp = client.post(
        "/users",
        json={
            "email": f"sync-{uuid4().hex[:8]}@example.com",
            "password": "integration-password-1",
            "team_id": team_id,
        },
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, f"user creation failed: {resp.text}"
    return resp.json()


@pytest.mark.asyncio
async def test_user_creation_syncs_membership_to_team_region(
    client, admin_token, litellm_region, make_team
):
    team = make_team()
    user = _create_user(client, admin_token, team["id"])

    members = await _team_member_ids(
        LITELLM_A_URL, litellm_region.name, team["id"]
    )
    assert str(user["id"]) in members, (
        f"user {user['id']} not a member of the LiteLLM team after create: "
        f"{members}"
    )


@pytest.mark.asyncio
async def test_remove_user_from_team_removes_litellm_membership(
    client, admin_token, litellm_region, make_team
):
    team = make_team()
    user = _create_user(client, admin_token, team["id"])
    assert str(user["id"]) in await _team_member_ids(
        LITELLM_A_URL, litellm_region.name, team["id"]
    )

    resp = client.post(
        f"/users/{user['id']}/remove-from-team", headers=_auth(admin_token)
    )
    assert resp.status_code == 200, resp.text

    members = await _team_member_ids(
        LITELLM_A_URL, litellm_region.name, team["id"]
    )
    assert str(user["id"]) not in members


@pytest.mark.asyncio
async def test_dedicated_region_gets_team_and_members_only_on_association(
    client, admin_token, litellm_region, dedicated_region, make_team
):
    team = make_team()  # created in shared region A
    user = _create_user(client, admin_token, team["id"])

    # Before association the dedicated proxy must not know the team.
    service_b = LiteLLMService(LITELLM_B_URL, LITELLM_MASTER_KEY)
    lt_team_b = LiteLLMService.format_team_id(dedicated_region.name, team["id"])
    with pytest.raises(HTTPException):
        await service_b.get_team_info(lt_team_b)

    resp = client.post(
        f"/regions/{dedicated_region.id}/teams/{team['id']}",
        headers=_auth(admin_token),
    )
    assert resp.status_code in (200, 201), resp.text

    members = await _team_member_ids(
        LITELLM_B_URL, dedicated_region.name, team["id"]
    )
    assert str(user["id"]) in members, (
        "association must bootstrap the team on the dedicated proxy and "
        f"backfill members; got {members}"
    )
