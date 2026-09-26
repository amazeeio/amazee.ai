from decimal import Decimal

import pytest

from app.registry.importer import AlreadyImported, import_deployments
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModel,
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
    assert registry_db.query(DBRegistryModelRegion).filter_by(model_id=first.model_id).count() == 2

    assert {g.slug for g in registry_db.query(DBRegistryAccessGroup)} == {"preview", "ga"}
    assert registry_db.query(DBRegistryModelRegionGroup).count() == 3

    proxy = registry_db.get(DBRegistryProxy, proxy_region.id)
    assert proxy.litellm_version == "1.102.1"
    assert proxy.aws_region_name == "us-east-1"
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
