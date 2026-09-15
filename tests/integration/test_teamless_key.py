"""A key whose owner has no team must work against a real LiteLLM proxy.

LiteLLM loads the key's team on every route, so a key carrying a team id that
was never created on the proxy is rejected with "Team doesn't exist in db".
This pins that such a key is created with no team id at all.
"""

from uuid import uuid4

from tests.integration.conftest import LITELLM_A_URL, _auth, completion


def test_key_for_teamless_owner_can_call_the_proxy(client, admin_token, litellm_region):
    suffix = uuid4().hex[:8]
    user_resp = client.post(
        "/users",
        json={"email": f"int-teamless-{suffix}@example.com", "password": "TestPass123!"},
        headers=_auth(admin_token),
    )
    assert user_resp.status_code == 201, f"user creation failed: {user_resp.text}"
    owner = user_resp.json()
    assert owner["team_id"] is None

    key_resp = client.post(
        "/private-ai-keys",
        json={
            "region_id": litellm_region.id,
            "owner_id": owner["id"],
            "name": f"int-teamless-key-{suffix}",
        },
        headers=_auth(admin_token),
    )
    assert key_resp.status_code == 200, f"key creation failed: {key_resp.text}"

    resp = completion(LITELLM_A_URL, key_resp.json()["litellm_token"])
    assert resp.status_code == 200, resp.text
