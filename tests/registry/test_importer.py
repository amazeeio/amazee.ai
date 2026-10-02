from decimal import Decimal

import pytest

from app.registry.importer import AlreadyImported, import_deployments
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)

DEPLOYMENTS = [
    {
        "model_name": "claude-x",
        "litellm_params": {
            "model": "bedrock/us.anthropic.claude-x-v1:0",
            "litellm_credential_name": "AWS Bedrock",
            "input_cost_per_token": 4e-06,
        },
        "model_info": {"id": "dep-1", "mode": "chat", "access_groups": ["preview", "ga"]},
    },
    {
        # Same model under a second name: one model row, two deployments.
        "model_name": "claude-x-alias",
        "litellm_params": {"model": "bedrock/anthropic.claude-x-v1:0"},
        "model_info": {"id": "dep-2", "access_groups": ["preview"]},
    },
    {
        "model_name": "llama",
        "litellm_params": {"model": "deepinfra/meta-llama/Llama-3"},
        "model_info": {"id": "dep-3"},
    },
    {
        "model_name": "no-prefix",
        "litellm_params": {"model": "amazon.titan-embed-text-v2:0"},
        "model_info": {"id": "dep-4"},
    },
]


def test_import_creates_registry_rows(registry_db, proxy_region):
    stats = import_deployments(
        registry_db, proxy_region, DEPLOYMENTS, "1.102.1", {"AWS Bedrock": "us-east-1"}
    )
    registry_db.commit()

    assert stats == {"deployments": 4, "imported": 3, "skipped": ["no-prefix"]}
    assert {p.name for p in registry_db.query(DBRegistryProvider)} == {"bedrock", "deepinfra"}
    models = {m.model_id: m for m in registry_db.query(DBRegistryModel)}
    assert set(models) == {"anthropic.claude-x-v1:0", "meta-llama/Llama-3"}
    assert models["anthropic.claude-x-v1:0"].source == "proxy"
    assert models["anthropic.claude-x-v1:0"].mode == "chat"

    first = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="dep-1").one()
    assert first.litellm_model == "bedrock/us.anthropic.claude-x-v1:0"
    assert first.input_cost_per_token == Decimal("4e-06")
    assert first.prices == {"input_cost_per_token": 4e-06}
    assert registry_db.query(DBRegistryModelRegion).filter_by(model_id=first.model_id).count() == 2

    assert {g.slug for g in registry_db.query(DBRegistryAccessGroup)} == {"preview", "ga"}
    assert registry_db.query(DBRegistryModelRegionGroup).count() == 3

    proxy = registry_db.get(DBRegistryProxy, proxy_region.id)
    assert proxy.litellm_version == "1.102.1"
    assert proxy.cloud_regions == {"bedrock": "us-east-1"}
    assert proxy.imported_at is not None


def test_second_import_is_refused(registry_db, proxy_region):
    import_deployments(registry_db, proxy_region, DEPLOYMENTS[:1], None, {})
    registry_db.commit()

    with pytest.raises(AlreadyImported):
        import_deployments(registry_db, proxy_region, DEPLOYMENTS, None, {})


def test_import_uses_base_model_and_ignores_duplicate_groups(registry_db, proxy_region):
    deployments = [
        {
            "model_name": "gpt-4o",
            "litellm_params": {"model": "azure/our-gpt4o-deployment"},
            "model_info": {"id": "az-1", "base_model": "azure/gpt-4o", "access_groups": ["ga", "ga"]},
        }
    ]

    import_deployments(registry_db, proxy_region, deployments, None, {})
    registry_db.commit()

    assert registry_db.query(DBRegistryModel).one().model_id == "gpt-4o"
    assert registry_db.query(DBRegistryModelRegion).one().litellm_model == "azure/our-gpt4o-deployment"
    assert registry_db.query(DBRegistryModelRegionGroup).count() == 1


def test_zero_prices_from_model_info_are_stored_as_unknown(registry_db, proxy_region):
    deployments = [
        {
            "model_name": "unknown",
            "litellm_params": {"model": "bedrock/openai.gpt-new"},
            "model_info": {"id": "u-1", "input_cost_per_token": 0, "output_cost_per_token": 0.0},
        },
        {
            "model_name": "known",
            "litellm_params": {"model": "bedrock/amazon.priced"},
            "model_info": {"id": "k-1", "input_cost_per_token": 0, "output_cost_per_token": 2e-06},
        },
    ]

    import_deployments(registry_db, proxy_region, deployments, None, {})
    registry_db.commit()

    models = {m.model_id: m for m in registry_db.query(DBRegistryModel)}
    assert models["openai.gpt-new"].input_cost_per_token is None
    assert models["openai.gpt-new"].output_cost_per_token is None
    assert models["openai.gpt-new"].prices == {}
    assert models["amazon.priced"].output_cost_per_token == Decimal("2e-06")
    assert models["amazon.priced"].prices == {"input_cost_per_token": 0, "output_cost_per_token": 2e-06}


def test_import_leaves_eol_date_to_the_eol_step(registry_db, proxy_region):
    deployments = [
        {
            "model_name": "old",
            "litellm_params": {"model": "bedrock/amazon.old"},
            "model_info": {"id": "o-1", "deprecation_date": "2027-01-31"},
        }
    ]

    import_deployments(registry_db, proxy_region, deployments, None, {})
    registry_db.commit()

    assert registry_db.query(DBRegistryModel).one().eol_date is None


def test_cloud_regions_per_provider(registry_db, proxy_region):
    deployments = [
        {"model_name": "a", "litellm_params": {"model": "bedrock/x", "aws_region_name": "eu-central-1"}, "model_info": {"id": "1"}},
        {"model_name": "b", "litellm_params": {"model": "bedrock_mantle/y", "litellm_credential_name": "AWS"}, "model_info": {"id": "2"}},
        {"model_name": "c", "litellm_params": {"model": "vertex_ai/gemini", "vertex_location": "europe-west4"}, "model_info": {"id": "3"}},
        {"model_name": "d", "litellm_params": {"model": "deepinfra/m"}, "model_info": {"id": "4"}},
    ]

    import_deployments(registry_db, proxy_region, deployments, None, {"AWS": "eu-central-1"})
    registry_db.commit()

    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {
        "bedrock": "eu-central-1",
        "bedrock_mantle": "eu-central-1",
        "vertex_ai": "europe-west4",
    }


def test_import_stores_the_proxy_price_as_base_scope(registry_db, proxy_region):
    deployments = [
        {
            "model_name": "known",
            "litellm_params": {"model": "bedrock/amazon.priced"},
            "model_info": {"id": "k-1", "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
        },
        {
            "model_name": "unknown",
            "litellm_params": {"model": "bedrock/openai.gpt-new"},
            "model_info": {"id": "u-1", "input_cost_per_token": 0, "output_cost_per_token": 0},
        },
    ]

    import_deployments(registry_db, proxy_region, deployments, None, {})
    registry_db.commit()

    price = registry_db.query(DBRegistryModelPrice).one()
    assert (price.scope_kind, price.scope, price.source) == ("base", "", "proxy")
    assert price.prices == {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}
