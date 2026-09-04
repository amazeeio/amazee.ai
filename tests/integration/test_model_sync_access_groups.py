"""Model catalog sync and access groups against a real LiteLLM proxy.

Uses its own sync-test-* model namespace. Note reconcile_region_models only
converges models with DBModelRegion catalog rows, so the session-scoped
mock-gpt (registered directly, no catalog row) is never deleted by these
tests. sync_model_to_region_task opens its own DB session via get_db(), so
catalog rows must be committed before reconciling.
"""

from uuid import uuid4

import pytest

from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBModelAliasTarget,
    DBModelRegion,
)
from app.services.access_groups import sync_region_access_group_entities
from app.services.model_sync import reconcile_region_models
from app.services.litellm import LiteLLMService
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    MOCK_INPUT_COST_PER_TOKEN,
    MOCK_OUTPUT_COST_PER_TOKEN,
    completion,
)


def _catalog_model(db, region, name, is_alias=False):
    model = DBModel(
        model_id=name,
        display_name=name,
        provider="openai",
        type="chat",
        is_active_globally=True,
        is_alias=is_alias,
        litellm_params=None
        if is_alias
        else {
            "model": f"openai/{name}",
            "api_key": "fake-upstream-key",
            "mock_response": "Hello from the catalog mock.",
            "input_cost_per_token": MOCK_INPUT_COST_PER_TOKEN,
            "output_cost_per_token": MOCK_OUTPUT_COST_PER_TOKEN,
        },
    )
    db.add(model)
    db.flush()
    db.add(DBModelRegion(model_id=model.id, region_id=region.id, is_active=True))
    db.commit()
    db.refresh(model)
    return model


async def _proxy_model_names(service) -> set:
    info = await service.get_model_info()
    return {
        e.get("model_name") for e in info.get("data", []) if isinstance(e, dict)
    }


@pytest.mark.asyncio
async def test_reconcile_registers_and_deregisters_catalog_model(
    db, litellm_region
):
    name = f"sync-test-{uuid4().hex[:8]}"
    model = _catalog_model(db, litellm_region, name)
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)

    result = await reconcile_region_models(db, litellm_region)
    assert result["models_resynced"] == 1
    assert name in await _proxy_model_names(service)

    assoc = (
        db.query(DBModelRegion)
        .filter_by(model_id=model.id, region_id=litellm_region.id)
        .first()
    )
    db.refresh(assoc)
    assert assoc.sync_status == "synced", assoc.sync_error

    # A second sweep must be a no-op: keys the proxy injects on its own must
    # not read as params drift, or every deployment is recreated each sweep.
    result = await reconcile_region_models(db, litellm_region)
    assert result["models_resynced"] == 0

    # Deactivate in the catalog -> reconcile must deregister on the proxy.
    assoc.is_active = False
    assoc.sync_status = "synced"  # simulate drift: proxy still has it
    db.commit()
    result = await reconcile_region_models(db, litellm_region)
    assert result["models_resynced"] == 1
    assert name not in await _proxy_model_names(service)


@pytest.mark.asyncio
async def test_alias_map_sync_and_synthetic_expansion(
    db, litellm_region, make_team, make_key
):
    target_name = f"sync-test-{uuid4().hex[:8]}"
    alias_name = f"sync-alias-{uuid4().hex[:8]}"
    target = _catalog_model(db, litellm_region, target_name)
    alias = _catalog_model(db, litellm_region, alias_name, is_alias=True)
    db.add(
        DBModelAliasTarget(
            alias_model_id=alias.id,
            region_id=litellm_region.id,
            target_model_id=target.id,
        )
    )
    db.commit()

    await reconcile_region_models(db, litellm_region)

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    settings = await service.get_router_settings()
    alias_map = {}
    for field in settings.get("fields") or []:
        if field.get("field_name") == "model_group_alias":
            value = field.get("field_value") or {}
            alias_map = {
                a: (t.get("model") if isinstance(t, dict) else t)
                for a, t in value.items()
            }
    assert alias_map.get(alias_name) == target_name, (
        f"alias map not written to router settings: {alias_map}"
    )

    # A completion under the ALIAS name must be served (synthetic expansion
    # of the alias to its target -- recent bug territory in the alias-map
    # handling, worth pinning end to end).
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    resp = completion(LITELLM_A_URL, key["litellm_token"])
    assert resp.status_code == 200  # sanity: key works at all
    resp = completion(LITELLM_A_URL, key["litellm_token"], model=alias_name)
    assert resp.status_code == 200, (
        f"completion via alias '{alias_name}' failed: {resp.text}"
    )


@pytest.mark.asyncio
async def test_access_group_entities_mirrored_to_proxy(db, litellm_region):
    name = f"sync-test-{uuid4().hex[:8]}"
    slug = f"sync-group-{uuid4().hex[:8]}"
    model = _catalog_model(db, litellm_region, name)

    group = DBModelAccessGroup(slug=slug, label=slug)
    db.add(group)
    db.flush()
    db.add(DBModelAccessGroupModel(group_id=group.id, model_id=model.id))
    db.add(
        DBModelAccessGroupRegion(group_id=group.id, region_id=litellm_region.id)
    )
    db.commit()

    await reconcile_region_models(db, litellm_region)
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    await sync_region_access_group_entities(db, litellm_region, service=service)

    groups = {g["access_group_name"]: g for g in await service.list_access_groups()}
    assert slug in groups, f"access group not mirrored to proxy: {list(groups)}"
    assert name in (groups[slug].get("access_model_names") or [])

    # Removing the region association must delete the managed entity.
    db.query(DBModelAccessGroupRegion).filter_by(group_id=group.id).delete()
    db.commit()
    await sync_region_access_group_entities(db, litellm_region, service=service)
    groups_after = {
        g["access_group_name"] for g in await service.list_access_groups()
    }
    assert slug not in groups_after
