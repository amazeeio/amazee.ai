from app.registry.models import DBRegistryModelRegion, DBRegistryModelSupport
from tests.registry.api.conftest import CHECKED_AT, auth, model


def _proxy(name, priced=None, supported=None, region_available=None, checked_at=None, enabled=None):
    return {
        "proxy": name, "priced": priced, "supported": supported, "region_available": region_available,
        "checked_at": checked_at.isoformat().replace("+00:00", "Z") if checked_at else None, "enabled": enabled,
    }


def _by_key(response):
    assert response.status_code == 200, response.text
    return {f"{m['provider']}/{m['model_id']}": m for m in response.json()}


def test_models_show_their_result_per_proxy(client, admin_token, deployed):
    body = _by_key(client.get("/registry/models", headers=auth(admin_token)))

    assert body["openai/gpt-4o"]["proxies"] == [
        _proxy("local-eu1", priced=True, supported=False, checked_at=CHECKED_AT, enabled=False),
        _proxy("local-us1", priced=True, supported=True, region_available=True, checked_at=CHECKED_AT, enabled=True),
    ]
    assert body["bedrock/anthropic.claude-x-v1:0"]["proxies"] == [
        _proxy("local-us1", priced=True, checked_at=CHECKED_AT, enabled=True),
    ]
    assert body["openai/gpt-old"]["proxies"] == []


def test_model_proxy_result_without_support_row_or_deployment(client, admin_token, deployed, registry_db):
    old = model(registry_db, "openai", "gpt-old")
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    registry_db.add_all([
        # Two deployments on one proxy: one enabled one is enough.
        DBRegistryModelRegion(
            model_id=old.id, region_id=deployed["eu"].id, model_name="a", litellm_model="openai/gpt-old",
            litellm_deployment_id="eu-old-a", enabled=False,
        ),
        DBRegistryModelRegion(
            model_id=old.id, region_id=deployed["eu"].id, model_name="b", litellm_model="openai/gpt-old",
            litellm_deployment_id="eu-old-b", enabled=True,
        ),
        DBRegistryModelSupport(model_id=claude.id, region_id=deployed["eu"].id, priced=False, checked_at=CHECKED_AT),
    ])
    registry_db.commit()

    body = _by_key(client.get("/registry/models", headers=auth(admin_token)))

    assert body["openai/gpt-old"]["proxies"] == [_proxy("local-eu1", enabled=True)]
    assert body["bedrock/anthropic.claude-x-v1:0"]["proxies"][0] == _proxy(
        "local-eu1", priced=False, checked_at=CHECKED_AT
    )


def test_models_total_count_header(client, admin_token, deployed):
    response = client.get("/registry/models?limit=1", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.headers["X-Total-Count"] == "3"
