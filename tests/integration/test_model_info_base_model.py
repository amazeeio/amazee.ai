"""Catalog model_info.base_model against a real LiteLLM proxy.

The backend id is deliberately one LiteLLM's cost map does not know (like
bedrock/zai.glm-4.6 in production) and litellm_params carry no pricing: only
base_model can give the deployment pricing, capabilities and real spend.
"""

from uuid import uuid4

import pytest

from app.db.models import DBModel, DBModelRegion
from app.services.litellm import LiteLLMService
from app.services.model_sync import reconcile_region_models
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    completion,
    wait_for_key_spend,
)

BASE_MODEL = "gpt-4o-mini"  # long-lived cost-map entry with vision + tools


@pytest.mark.asyncio
async def test_base_model_supplies_pricing_capabilities_and_spend(
    db, litellm_region, make_team, make_key
):
    name = f"base-model-test-{uuid4().hex[:8]}"
    model = DBModel(
        model_id=name,
        display_name=name,
        provider="openai",
        type="chat",
        is_active_globally=True,
        litellm_params={
            "model": f"openai/amazee-unknown-{name}",
            "api_key": "fake-upstream-key",
            "mock_response": "Hello from an unknown backend.",
        },
        model_info={"base_model": BASE_MODEL},
    )
    db.add(model)
    db.flush()
    db.add(DBModelRegion(model_id=model.id, region_id=litellm_region.id, is_active=True))
    db.commit()

    await reconcile_region_models(db, litellm_region)

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    [info] = [
        e["model_info"]
        for e in (await service.get_model_info())["data"]
        if e.get("model_name") == name
    ]
    assert info.get("base_model") == BASE_MODEL
    assert info.get("mode") == "chat"
    assert info.get("input_cost_per_token"), info
    assert info.get("output_cost_per_token"), info
    assert info.get("supports_vision") is True
    assert info.get("supports_function_calling") is True

    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    resp = completion(LITELLM_A_URL, key["litellm_token"], model=name)
    assert resp.status_code == 200, resp.text
    # Without base_model this backend prices at $0 and the key never accrues.
    assert await wait_for_key_spend(LITELLM_A_URL, key["litellm_token"]) > 0
