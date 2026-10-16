from datetime import timedelta

from app.core.config import settings
from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
)
from tests.registry.api.conftest import CHECKED_AT, TODAY, auth, model


def test_providers_with_proxies_and_model_counts(client, admin_token, deployed, registry_db):
    registry_db.add(DBRegistryProvider(name="azure"))
    registry_db.commit()

    response = client.get("/registry/providers", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    assert response.json() == [
        {"provider": "azure", "proxies": [], "models": 0, "deployed_models": 0},
        {"provider": "bedrock", "proxies": ["local-us1"], "models": 1, "deployed_models": 1},
        # gpt-4o is on two proxies but counts once; gpt-old is not deployed.
        {"provider": "openai", "proxies": ["local-eu1", "local-us1"], "models": 2, "deployed_models": 1},
    ]


def test_proxies_list(client, admin_token, deployed, registry_db):
    us = registry_db.get(DBRegistryProxy, deployed["us"].id)
    us.litellm_version, us.imported_at = "1.80.0", CHECKED_AT
    registry_db.commit()

    response = client.get("/registry/proxies", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "2"
    assert response.json() == [
        {
            "proxy": "local-eu1", "region_id": deployed["eu"].id, "litellm_version": None, "cloud_regions": {},
            "imported_at": None, "providers": ["openai"], "owner": "catalog",
            # gpt-4o is not supported there, and its deployment is disabled.
            "models_supported": 0, "models_enabled": 0,
        },
        {
            "proxy": "local-us1", "region_id": deployed["us"].id, "litellm_version": "1.80.0",
            "cloud_regions": {"bedrock": "us-east-1"}, "imported_at": "2026-01-02T03:04:05Z",
            "providers": ["bedrock", "openai"], "owner": "catalog",
            # A null support result is not counted as supported.
            "models_supported": 1, "models_enabled": 2,
        },
    ]


def test_proxies_owner_follows_catalog_managed_regions(client, admin_token, deployed, monkeypatch):
    monkeypatch.setattr(settings, "ENV_SUFFIX", "production")
    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", "")
    body = client.get("/registry/proxies", headers=auth(admin_token)).json()
    assert [(p["proxy"], p["owner"]) for p in body] == [("local-eu1", "none"), ("local-us1", "none")]

    monkeypatch.setattr(settings, "CATALOG_MANAGED_REGIONS", "local-eu1")
    body = client.get("/registry/proxies", headers=auth(admin_token)).json()
    assert [(p["proxy"], p["owner"]) for p in body] == [("local-eu1", "catalog"), ("local-us1", "none")]


def test_proxy_detail(client, admin_token, deployed, registry_db):
    gpt = model(registry_db, "openai", "gpt-4o")
    old = model(registry_db, "openai", "gpt-old")
    deployment = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    group = DBRegistryAccessGroup(slug="team-a")
    registry_db.add(group)
    registry_db.flush()
    registry_db.add_all([
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=group.id),
        # A support row with no deployment still lists the model.
        DBRegistryModelSupport(
            model_id=old.id, region_id=deployed["us"].id, priced=False, supported=False,
            checked_at=CHECKED_AT + timedelta(hours=1),
        ),
    ])
    gpt.eol_date = TODAY
    registry_db.commit()

    response = client.get("/registry/proxies/local-us1", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proxy"] == "local-us1"
    assert body["providers"] == ["bedrock", "openai"]
    assert body["checked_at"] == "2026-01-02T04:04:05Z"
    assert [m["key"] for m in body["models"]] == [
        "bedrock/anthropic.claude-x-v1:0", "openai/gpt-4o", "openai/gpt-old",
    ]
    claude, gpt_entry, old_entry = body["models"]
    assert gpt_entry["status"] == "active" and gpt_entry["eol_date"] == "2026-01-01"
    assert gpt_entry["support"] == {
        "proxy": "local-us1", "priced": True, "supported": True, "region_available": True,
        "checked_at": "2026-01-02T03:04:05Z", "enabled": True,
    }
    assert gpt_entry["deployments"] == [{
        "proxy": "local-us1", "model_name": "gpt-4o", "litellm_model": "openai/gpt-4o", "enabled": True,
        "input_cost_per_token": None, "output_cost_per_token": None, "prices": {}, "access_groups": ["team-a"],
    }]
    assert claude["support"]["supported"] is None
    assert [d["model_name"] for d in claude["deployments"]] == ["claude-x"]
    assert claude["deployments"][0]["access_groups"] == []
    assert old_entry["status"] == "removed"
    assert old_entry["support"]["enabled"] is None
    assert old_entry["deployments"] == []


def test_proxy_detail_unknown_is_404(client, admin_token, deployed, registry_db):
    # A region with no registry_proxies row is not a registry proxy.
    registry_db.add(DBRegion(
        name="local-xx1", litellm_api_url="http://litellm-xx:4000", litellm_api_key="sk-test", is_active=True,
    ))
    registry_db.commit()

    for name in ("nope", "local-xx1"):
        response = client.get(f"/registry/proxies/{name}", headers=auth(admin_token))
        assert response.status_code == 404
        assert response.json()["detail"] == "Proxy not found"
