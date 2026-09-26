from datetime import date
from unittest.mock import patch

import httpx

from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryRun,
)
from app.registry.support import run_support_check

# What the proxy loaded: it can price "priced" (as a geo entry) and nothing else
# of ours. The entry for its "retired" deployment has no price, as LiteLLM adds it.
PROXY_LIST = {
    "us.anthropic.priced-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 1e-06},
    "bedrock/anthropic.retired-v1:0": {"litellm_provider": "bedrock", "mode": "chat"},
    "gpt-4o": {"litellm_provider": "openai", "input_cost_per_token": 1e-06},
}


def _seed(db, region):
    provider = DBRegistryProvider(name="bedrock")
    db.add(provider)
    db.flush()
    ids = {}
    for model_id in ("anthropic.priced-v1:0", "anthropic.retired-v1:0", "anthropic.new-v1:0"):
        row = DBRegistryModel(
            provider_id=provider.id,
            model_id=model_id,
            supports=[],
            status="active",
            source="litellm",
            first_seen=date(2026, 9, 1),
            last_seen=date(2026, 9, 1),
        )
        db.add(row)
        db.flush()
        ids[model_id] = row.id
    for model_id, name in (("anthropic.priced-v1:0", "priced"), ("anthropic.retired-v1:0", "retired")):
        db.add(
            DBRegistryModelRegion(
                model_id=ids[model_id],
                region_id=region.id,
                model_name=name,
                litellm_model=f"bedrock/{model_id}",
                enabled=True,
            )
        )
    db.commit()
    return ids


def test_support_check_compares_proxy_list(registry_db, proxy_region):
    ids = _seed(registry_db, proxy_region)
    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST) as fetch:
        stats = run_support_check(registry_db)

    fetch.assert_called_once_with("http://litellm:4000/public/litellm_model_cost_map")
    assert stats["regions"]["local-us1"] == {
        "priced": 1,
        "unpriced": 2,
        "deployed_unpriced": ["retired"],
    }
    support = {
        row.model_id: row.priced
        for row in registry_db.query(DBRegistryModelSupport).filter_by(region_id=proxy_region.id)
    }
    assert support == {
        ids["anthropic.priced-v1:0"]: True,
        ids["anthropic.retired-v1:0"]: False,
        ids["anthropic.new-v1:0"]: False,
    }

    # After a restart the proxy knows the new model; the same rows are updated.
    restarted = {**PROXY_LIST, "anthropic.new-v1:0": {"litellm_provider": "bedrock", "output_cost_per_token": 0}}
    with patch("app.registry.support.fetch_model_list", return_value=restarted):
        run_support_check(registry_db)
    assert registry_db.query(DBRegistryModelSupport).count() == 3
    new = registry_db.get(DBRegistryModelSupport, (ids["anthropic.new-v1:0"], proxy_region.id))
    assert new.priced is True


def test_unreachable_proxy_keeps_its_last_result(registry_db, proxy_region):
    _seed(registry_db, proxy_region)
    other = DBRegion(name="down", litellm_api_url="http://down:4000", litellm_api_key="k", is_active=True)
    registry_db.add(other)
    registry_db.commit()
    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        run_support_check(registry_db)
    before = {
        (r.model_id, r.priced, r.checked_at)
        for r in registry_db.query(DBRegistryModelSupport).filter_by(region_id=other.id)
    }

    def fetch(url):
        if "down" in url:
            raise httpx.ConnectError("refused")
        return PROXY_LIST

    with patch("app.registry.support.fetch_model_list", side_effect=fetch):
        stats = run_support_check(registry_db)

    assert "down" in stats["unreachable"] and "local-us1" in stats["regions"]
    after = {
        (r.model_id, r.priced, r.checked_at)
        for r in registry_db.query(DBRegistryModelSupport).filter_by(region_id=other.id)
    }
    assert after == before
    assert registry_db.query(DBRegistryRun).filter_by(status="ok").count() == 2
