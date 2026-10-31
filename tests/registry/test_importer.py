from datetime import date
from decimal import Decimal

import pytest

from app.registry.importer import AlreadyImported, adopt_aliases, adopt_credentials, import_deployments
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryCredential,
    DBRegistryCredentialRegion,
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

    assert stats == {"deployments": 4, "imported": 3, "skipped": ["no-prefix"], "aliases": []}
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


def test_cloud_regions_ignore_environment_references(registry_db, proxy_region):
    deployments = [
        {
            "model_name": "a",
            "litellm_params": {
                "model": "bedrock/x",
                "aws_region_name": "os.environ/AWS_REGION_NAME",
                "litellm_credential_name": "AWS",
            },
            "model_info": {"id": "1"},
        },
        {"model_name": "b", "litellm_params": {"model": "vertex_ai/gemini", "vertex_location": "os.environ/VERTEX_LOCATION"}, "model_info": {"id": "2"}},
        {"model_name": "c", "litellm_params": {"model": "bedrock_mantle/y", "litellm_credential_name": "ENV"}, "model_info": {"id": "3"}},
    ]

    import_deployments(
        registry_db, proxy_region, deployments, None, {"AWS": "eu-central-1", "ENV": "os.environ/AWS_REGION"}
    )
    registry_db.commit()

    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {"bedrock": "eu-central-1"}


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


def test_import_reads_a_provider_alias_prefix(registry_db, proxy_region):
    deployment = {
        "model_name": "claude-x-converse",
        "litellm_params": {"model": "bedrock_converse/us.anthropic.claude-x-v1:0"},
        "model_info": {"id": "dep-5"},
    }
    stats = import_deployments(registry_db, proxy_region, [deployment], None, {})
    registry_db.commit()

    assert stats["imported"] == 1 and stats["skipped"] == []
    model = registry_db.query(DBRegistryModel).one()
    assert model.model_id == "anthropic.claude-x-v1:0"
    assert registry_db.get(DBRegistryProvider, model.provider_id).name == "bedrock"


def test_import_takes_the_provider_from_model_info_without_prefix(registry_db, proxy_region):
    deployment = {
        "model_name": "titan-embed",
        "litellm_params": {"model": "amazon.titan-embed-text-v2:0", "aws_region_name": "us-east-1"},
        "model_info": {"id": "t-1", "litellm_provider": "bedrock", "mode": "embedding"},
    }
    stats = import_deployments(registry_db, proxy_region, [deployment], None, {})
    registry_db.commit()

    assert stats["imported"] == 1 and stats["skipped"] == []
    model = registry_db.query(DBRegistryModel).one()
    assert model.model_id == "amazon.titan-embed-text-v2:0"
    assert registry_db.get(DBRegistryProvider, model.provider_id).name == "bedrock"
    assert registry_db.query(DBRegistryModelRegion).one().litellm_model == "amazon.titan-embed-text-v2:0"
    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {"bedrock": "us-east-1"}


def test_import_skips_router_alias_copies_of_a_deployment(registry_db, proxy_region):
    params = {"model": "bedrock/us.anthropic.claude-x-v1:0"}
    deployments = [
        {"model_name": "claude-x", "litellm_params": params, "model_info": {"id": "dep-1"}},
        # LiteLLM lists a model_group_alias as a copy of its target, with the same id.
        {"model_name": "chat", "litellm_params": params, "model_info": {"id": "dep-1"}},
    ]
    stats = import_deployments(registry_db, proxy_region, deployments, None, {}, aliases={"chat"})
    registry_db.commit()

    assert stats == {"deployments": 2, "imported": 1, "skipped": [], "aliases": ["chat"]}
    assert registry_db.query(DBRegistryModelRegion).one().model_name == "claude-x"


def test_import_keeps_a_real_deployment_whose_name_is_an_alias(registry_db, proxy_region):
    params = {"model": "bedrock/us.anthropic.claude-x-v1:0"}
    deployments = [
        {"model_name": "claude-x", "litellm_params": params, "model_info": {"id": "dep-1"}},
        {"model_name": "old-name", "litellm_params": params, "model_info": {"id": "dep-9"}},
    ]
    stats = import_deployments(registry_db, proxy_region, deployments, None, {}, aliases={"old-name"})
    registry_db.commit()

    assert stats["aliases"] == []
    assert {r.model_name for r in registry_db.query(DBRegistryModelRegion)} == {"claude-x", "old-name"}


def test_import_skips_a_self_alias_copy_with_the_same_name(registry_db, proxy_region):
    params = {"model": "bedrock/us.anthropic.claude-x-v1:0"}
    deployments = [
        {"model_name": "claude-x", "litellm_params": params, "model_info": {"id": "dep-1"}},
        {"model_name": "claude-x", "litellm_params": params, "model_info": {"id": "dep-1"}},
    ]
    stats = import_deployments(registry_db, proxy_region, deployments, None, {}, aliases={"claude-x"})
    registry_db.commit()

    assert stats["aliases"] == ["claude-x"]
    assert registry_db.query(DBRegistryModelRegion).one().model_name == "claude-x"


def test_import_keeps_deployment_config_without_secrets(registry_db, proxy_region):
    params = {
        "model": "bedrock/us.anthropic.claude-x-v1:0",
        "aws_region_name": "eu-central-1",
        "litellm_credential_name": "AWS Bedrock",
        "api_key": "sk-live-1234",
        "aws_secret_access_key": "os.environ/AWS_SECRET_ACCESS_KEY",
        "additional_drop_params": ["temperature"],
    }
    info = {"id": "c-1", "mode": "chat", "max_input_tokens": 200000, "supports_vision": True, "api_key": "sk-live-5678"}

    import_deployments(
        registry_db, proxy_region, [{"model_name": "claude-x", "litellm_params": params, "model_info": info}], None, {}
    )
    registry_db.commit()

    row = registry_db.query(DBRegistryModelRegion).one()
    assert row.litellm_params == {k: v for k, v in params.items() if k != "api_key"}
    assert row.model_info == {k: v for k, v in info.items() if k != "api_key"}


MANTLE_API_BASE = "https://bedrock-mantle.us-east-1.api.aws/openai/v1"


def test_import_stores_bedrock_mantle_deployments_under_bedrock_mantle(registry_db, proxy_region):
    mantle = DBRegistryProvider(name="bedrock_mantle")
    registry_db.add(mantle)
    registry_db.flush()
    seeded = DBRegistryModel(
        provider_id=mantle.id, model_id="openai.gpt-5.4", supports=[], status="active",
        source="litellm", first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 1),
    )
    registry_db.add(seeded)
    registry_db.flush()
    gpt_params = {
        "model": "openai/openai.gpt-5.4",
        "api_base": MANTLE_API_BASE,
        "api_key": "os.environ/AWS_BEDROCK_KEY",
        "input_cost_per_token": 1e-06,
    }
    deployments = [
        {"model_name": "gpt-5.4", "litellm_params": gpt_params, "model_info": {"id": "m-1"}},
        {
            "model_name": "kimi",
            "litellm_params": {
                "model": "openai/moonshotai.kimi-k2.5",
                "api_base": "https://bedrock-mantle.us-east-2.api.aws/v1",
            },
            "model_info": {"id": "m-2"},
        },
        {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"id": "o-1"}},
        {
            "model_name": "gpt-4.1",
            "litellm_params": {"model": "openai/gpt-4.1", "api_base": "https://api.openai.com/v1"},
            "model_info": {"id": "o-2"},
        },
        {
            "model_name": "gpt-5",
            "litellm_params": {"model": "openai/gpt-5", "api_base": "os.environ/OPENAI_API_BASE"},
            "model_info": {"id": "o-3"},
        },
    ]

    stats = import_deployments(registry_db, proxy_region, deployments, None, {})
    registry_db.commit()

    assert stats["skipped"] == []
    providers = {p.name: p.id for p in registry_db.query(DBRegistryProvider)}
    gpt = registry_db.query(DBRegistryModel).filter_by(
        provider_id=providers["bedrock_mantle"], model_id="openai.gpt-5.4"
    ).one()
    assert gpt.id == seeded.id
    assert registry_db.query(DBRegistryModel).filter_by(
        provider_id=providers["bedrock_mantle"], model_id="moonshotai.kimi-k2.5"
    ).count() == 1
    openai_ids = {m.model_id for m in registry_db.query(DBRegistryModel).filter_by(provider_id=providers["openai"])}
    assert openai_ids == {"gpt-4o", "gpt-4.1", "gpt-5"}

    deployment = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="m-1").one()
    assert deployment.model_id == seeded.id
    assert deployment.litellm_model == "openai/openai.gpt-5.4"
    assert deployment.litellm_params == gpt_params
    assert deployment.prices == {"input_cost_per_token": 1e-06}
    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {"bedrock_mantle": "us-east-1"}


@pytest.mark.parametrize(
    "params, credential_regions, expected",
    [
        ({"aws_region_name": "eu-central-1"}, {}, "eu-central-1"),
        ({"litellm_credential_name": "AWS"}, {"AWS": "eu-west-1"}, "eu-west-1"),
        ({}, {}, "us-east-1"),
        ({"aws_region_name": "os.environ/AWS_REGION"}, {}, "us-east-1"),
    ],
)
def test_mantle_cloud_region_comes_last(registry_db, proxy_region, params, credential_regions, expected):
    deployment = {
        "model_name": "gpt-5.4",
        "litellm_params": {"model": "openai/openai.gpt-5.4", "api_base": MANTLE_API_BASE, **params},
        "model_info": {"id": "m-1"},
    }
    import_deployments(registry_db, proxy_region, [deployment], None, credential_regions)
    registry_db.commit()

    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {"bedrock_mantle": expected}


def _registry_state(db):
    db.expire_all()
    return (
        sorted((r.litellm_deployment_id, r.model_name) for r in db.query(DBRegistryModelRegion)),
        sorted((a.alias, a.target, a.hidden) for a in db.query(DBRegistryAlias)),
        sorted((c.name, c.provider, c.values_encrypted, str(c.credential_info)) for c in db.query(DBRegistryCredential)),
        sorted((link.credential_id, link.region_id) for link in db.query(DBRegistryCredentialRegion)),
        db.get(DBRegistryProxy, db.query(DBRegistryProxy.region_id).scalar()).cloud_regions,
    )


def test_an_adopt_import_adds_only_new_deployments_and_keeps_the_rows(registry_db, proxy_region):
    import_deployments(registry_db, proxy_region, DEPLOYMENTS[:1], None, {})
    registry_db.commit()
    first = registry_db.query(DBRegistryModelRegion).one()
    first.model_name = "renamed-by-admin"
    imported_at = registry_db.get(DBRegistryProxy, proxy_region.id).imported_at
    registry_db.commit()

    stats = import_deployments(registry_db, proxy_region, DEPLOYMENTS, None, {}, adopt=True)
    registry_db.commit()

    assert (stats["kept"], stats["imported"], stats["skipped"]) == (1, 2, ["no-prefix"])
    assert sorted(r.litellm_deployment_id for r in registry_db.query(DBRegistryModelRegion)) == ["dep-1", "dep-2", "dep-3"]
    assert registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="dep-1").one().model_name == (
        "renamed-by-admin"
    )
    proxy = registry_db.get(DBRegistryProxy, proxy_region.id)
    assert proxy.imported_at == imported_at
    assert proxy.adopted_at is not None


def test_an_adopt_import_merges_cloud_regions_and_keeps_an_admin_value(registry_db, proxy_region):
    registry_db.add(DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "eu-west-1"}))
    registry_db.commit()
    vertex = {
        "model_name": "gemini", "litellm_params": {"model": "vertex_ai/gemini-x", "vertex_location": "us-central1"},
        "model_info": {"id": "dep-9"},
    }

    import_deployments(registry_db, proxy_region, [DEPLOYMENTS[0], vertex], None, {"AWS Bedrock": "us-east-1"}, adopt=True)

    assert registry_db.get(DBRegistryProxy, proxy_region.id).cloud_regions == {
        "bedrock": "eu-west-1", "vertex_ai": "us-central1",
    }


def test_adopt_fills_aliases_and_credentials_and_keeps_existing_rows(registry_db, proxy_region):
    import_deployments(registry_db, proxy_region, DEPLOYMENTS[:1], None, {})
    registry_db.add(DBRegistryAlias(region_id=proxy_region.id, alias="chat", target="claude-x", hidden=False))
    registry_db.commit()

    added = adopt_aliases(registry_db, proxy_region, {
        "chat": "llama", "fast": {"model": "claude-x", "hidden": True}, "plain": "claude-x", "bad": None,
    })
    created = adopt_credentials(registry_db, proxy_region, {"AWS Bedrock": {"aws_region_name": "us-east-1"}})
    registry_db.commit()

    assert (added, created) == (2, 1)
    assert sorted((a.alias, a.target, a.hidden) for a in registry_db.query(DBRegistryAlias)) == [
        ("chat", "claude-x", False), ("fast", "claude-x", True), ("plain", "claude-x", False),
    ]
    [credential] = registry_db.query(DBRegistryCredential).all()
    assert (credential.name, credential.provider, credential.values_encrypted, credential.credential_info) == (
        "AWS Bedrock", None, None, {"aws_region_name": "us-east-1"},
    )
    assert [(link.credential_id, link.region_id) for link in registry_db.query(DBRegistryCredentialRegion)] == [
        (credential.id, proxy_region.id)
    ]


def test_a_second_adopt_import_changes_nothing_and_a_plain_import_is_still_refused(registry_db, proxy_region):
    def adopt():
        import_deployments(registry_db, proxy_region, DEPLOYMENTS, None, {"AWS Bedrock": "us-east-1"}, adopt=True)
        adopt_aliases(registry_db, proxy_region, {"chat": "claude-x"})
        adopt_credentials(registry_db, proxy_region, {"AWS Bedrock": {}})
        registry_db.commit()

    adopt()
    before = _registry_state(registry_db)
    adopt()

    assert _registry_state(registry_db) == before
    with pytest.raises(AlreadyImported):
        import_deployments(registry_db, proxy_region, DEPLOYMENTS, None, {})
