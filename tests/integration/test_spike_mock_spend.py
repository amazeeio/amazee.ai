"""Phase-1 spike: proves the two assumptions the whole suite rests on.

(a) A mock_response model with explicit per-token pricing accrues real spend
    through LiteLLM's accounting pipeline (visible on /key/info).
(b) The spend flush lands fast enough (proxy_batch_write_at: 1) that budget
    caps actually start rejecting requests within a pollable window.

If either fails on a given LiteLLM image, the fallback is a fake-OpenAI
container — decide only then (see litellm-integration-tests-plan.md).
"""

import pytest

from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    completion,
    wait_for,
    wait_for_key_spend,
)


@pytest.mark.asyncio
async def test_mock_completion_accrues_spend(litellm_region, make_team, make_key):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    resp = completion(LITELLM_A_URL, token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["choices"][0]["message"]["content"]
    assert body["usage"]["total_tokens"] > 0

    # Behavioral assertion, not exact-float: token counts may drift between
    # LiteLLM versions; what must hold is that spend lands and is positive.
    spend = await wait_for_key_spend(LITELLM_A_URL, token)
    assert spend > 0


@pytest.mark.asyncio
async def test_key_budget_cap_blocks_requests(litellm_region, make_team, make_key):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    # One completion to learn this version's cost-per-call, then cap the key
    # just above it so the next call lands over the cap.
    assert completion(LITELLM_A_URL, token).status_code == 200
    cost_per_call = await wait_for_key_spend(LITELLM_A_URL, token)

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    await service.update_key_budget(token, max_budget=cost_per_call * 1.5)

    def blocked():
        resp = completion(LITELLM_A_URL, token)
        return resp if resp.status_code >= 400 else None

    resp = wait_for(
        blocked,
        timeout=30,
        message="proxy to reject the key once max_budget is exceeded",
    )
    # LiteLLM signals budget exhaustion with a 4xx (400/429 depending on
    # version); the contract we depend on is "requests stop succeeding".
    assert 400 <= resp.status_code < 500, resp.text
    assert "budget" in resp.text.lower()
