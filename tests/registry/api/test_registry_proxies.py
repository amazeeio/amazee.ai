from datetime import timedelta

import pytest

from app.core.config import settings
from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryCloudAvailability,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
    DBRegistrySyncRun,
)
from tests.registry.api.conftest import CHECKED_AT, TODAY, auth, model, ok


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
            "imported_at": None, "adopted_at": None, "providers": ["openai"], "owner": "catalog",
            # gpt-4o is not supported there, and its deployment is disabled.
            "models_supported": 0, "models_enabled": 0,
        },
        {
            "proxy": "local-us1", "region_id": deployed["us"].id, "litellm_version": "1.80.0",
            "cloud_regions": {"bedrock": "us-east-1"}, "imported_at": "2026-01-02T03:04:05Z", "adopted_at": None,
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
        "proxy": "local-us1", "model_name": "gpt-4o", "litellm_model": "openai/gpt-4o",
        "litellm_deployment_id": "us-gpt", "enabled": True,
        "input_cost_per_token": None, "output_cost_per_token": None, "prices": {}, "access_groups": ["team-a"],
        "credential": None,
        "synced_at": None, "changed_at": None, "sync_error": None,
    }]
    assert claude["support"]["supported"] is None
    assert [d["model_name"] for d in claude["deployments"]] == ["claude-x"]
    assert claude["deployments"][0]["access_groups"] == []
    assert claude["deployments"][0]["credential"] == "AWS Bedrock"
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


def _patch(client, token, proxy, body, query=""):
    return client.patch(f"/registry/proxies/{proxy}{query}", json=body, headers=auth(token))


def test_proxy_patch_sets_changes_and_removes_cloud_regions(client, admin_token, deployed, registry_db):
    body = ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"azure": "eastus"}}))
    assert body["cloud_regions"] == {"bedrock": "us-east-1", "azure": "eastus"}
    assert body == next(p for p in ok(client.get("/registry/proxies", headers=auth(admin_token))) if p["proxy"] == "local-us1")

    body = ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": "eu-west-1", "azure": None}}))
    assert body["cloud_regions"] == {"bedrock": "eu-west-1"}

    # A null for a provider that is not set changes nothing.
    body = ok(_patch(client, admin_token, "local-eu1", {"cloud_regions": {"vertex_ai": None}}))
    assert body["cloud_regions"] == {}
    registry_db.expire_all()
    assert registry_db.get(DBRegistryProxy, deployed["us"].id).cloud_regions == {"bedrock": "eu-west-1"}


def test_proxy_patch_unknown_is_404(client, admin_token, deployed, bare_region):
    for name in ("nope", bare_region.name):
        response = _patch(client, admin_token, name, {"cloud_regions": {"bedrock": "us-east-1"}})
        assert response.status_code == 404
        assert response.json()["detail"] == "Proxy not found"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"cloud_regions": {}},
        {"cloud_regions": None},
        {"cloud_regions": {"Bedrock": "us-east-1"}},
        {"cloud_regions": {"bedrock": "US-EAST-1"}},
        {"cloud_regions": {"x" * 65: "us-east-1"}},
        {"cloud_regions": {"bedrock": "x" * 65}},
        {"cloud_regions": {"bedrock": "us-east-1"}, "other": 1},
    ],
)
def test_proxy_patch_refuses_a_bad_body(client, admin_token, deployed, body):
    assert _patch(client, admin_token, "local-us1", body).status_code == 422


def test_proxy_patch_dry_run_saves_nothing(client, admin_token, deployed, registry_db):
    body = ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": None}}, "?dry_run=true"))

    assert body["steps"] == []
    assert body["result"]["proxy"] == "local-us1" and body["result"]["cloud_regions"] == {}
    registry_db.expire_all()
    assert registry_db.get(DBRegistryProxy, deployed["us"].id).cloud_regions == {"bedrock": "us-east-1"}


def test_proxy_patch_sets_region_available_again(client, admin_token, deployed, registry_db):
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    gpt = model(registry_db, "openai", "gpt-4o")
    common = {"provider": "bedrock", "source": "bedrock_community", "call_types": ["ON_DEMAND"], "last_seen": TODAY}
    registry_db.add_all([
        DBRegistryCloudAvailability(model_id=claude.model_id, cloud_region="us-east-1", **common),
        # eu-west-1 is a known region with no claude-x, so the model is not available there.
        DBRegistryCloudAvailability(model_id="anthropic.other-v1:0", cloud_region="eu-west-1", **common),
    ])
    registry_db.commit()

    def available(model_id):
        registry_db.expire_all()
        return registry_db.query(DBRegistryModelSupport).filter_by(
            model_id=model_id, region_id=deployed["us"].id
        ).one().region_available

    ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": "eu-west-1"}}))
    assert available(claude.id) is False
    ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": "us-east-1"}}))
    assert available(claude.id) is True
    ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": None}}))
    assert available(claude.id) is None
    # Another provider's support row stays as it is.
    assert available(gpt.id) is True

    ok(_patch(client, admin_token, "local-us1", {"cloud_regions": {"bedrock": "eu-west-1"}}, "?dry_run=true"))
    assert available(claude.id) is None


def test_proxy_owner_is_registry_for_a_managed_proxy(client, admin_token, deployed, managed_proxies):
    managed_proxies.add("local-us1")

    body = client.get("/registry/proxies", headers=auth(admin_token)).json()

    assert [(p["proxy"], p["owner"]) for p in body] == [("local-eu1", "catalog"), ("local-us1", "registry")]


def test_proxy_detail_shows_credentials_and_no_drift_with_no_repair(client, admin_token, writable):
    body = client.get("/registry/proxies/local-us1", headers=auth(admin_token)).json()

    assert body["credentials"] == ["AWS Bedrock", "c1", "c2"]
    assert (body["drift"], body["unmanaged"], body["failed"], body["drift_checked_at"]) == (None, None, None, None)


def test_proxy_detail_shows_the_drift_of_the_newest_repair(client, admin_token, registry_db, deployed):
    us_id = deployed["us"].id
    old = DBRegistrySyncRun(
        trigger="repair", status="ok", region_ids=[us_id], scope={}, finished_at=CHECKED_AT,
        stats={"proxies": {"local-us1": {"drift": [{"action": "old"}], "unmanaged": [], "failed": []}}},
    )
    new = DBRegistrySyncRun(
        trigger="daily", status="failed", region_ids=[us_id], scope={}, finished_at=CHECKED_AT + timedelta(days=1),
        stats={"proxies": {"local-us1": {"drift": [], "unmanaged": [{"deployment_id": "x9"}], "failed": ["claude-x"]}}},
    )
    # A write run and an unfinished repair do not count.
    write = DBRegistrySyncRun(
        trigger="write", status="ok", region_ids=[us_id], scope={}, finished_at=CHECKED_AT + timedelta(days=2),
        stats={"proxies": {"local-us1": {"drift": [{"action": "write"}], "unmanaged": []}}},
    )
    running = DBRegistrySyncRun(trigger="repair", status="running", region_ids=[us_id], scope={})
    registry_db.add_all([old, new, write, running])
    registry_db.commit()

    body = client.get("/registry/proxies/local-us1", headers=auth(admin_token)).json()

    assert (body["drift"], body["unmanaged"], body["failed"]) == ([], [{"deployment_id": "x9"}], ["claude-x"])
    assert body["drift_checked_at"] == "2026-01-03T03:04:05Z"
    assert client.get("/registry/proxies/local-eu1", headers=auth(admin_token)).json()["drift"] is None
