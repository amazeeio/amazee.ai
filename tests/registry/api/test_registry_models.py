from app.registry.models import DBRegistryModelPrice, DBRegistryModelRegion, DBRegistryModelSupport
from tests.registry.api.conftest import CHECKED_AT, TODAY, auth, model


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


def _keys(client, token, query):
    response = client.get(f"/registry/models?{query}", headers=auth(token))
    assert response.status_code == 200, response.text
    return [f"{m['provider']}/{m['model_id']}" for m in response.json()]


CLAUDE, GPT, OLD = "bedrock/anthropic.claude-x-v1:0", "openai/gpt-4o", "openai/gpt-old"


def test_models_family_and_proxy_filters(client, admin_token, deployed):
    assert _keys(client, admin_token, "family=claudex") == [CLAUDE]
    assert _keys(client, admin_token, "proxy=local-us1") == [CLAUDE, GPT]
    assert _keys(client, admin_token, "proxy=local-eu1") == [GPT]

    unknown = client.get("/registry/models?proxy=nope", headers=auth(admin_token))
    assert unknown.status_code == 200
    assert unknown.json() == []
    assert unknown.headers["X-Total-Count"] == "0"


def test_models_enabled_filter(client, admin_token, deployed):
    assert _keys(client, admin_token, "enabled=true") == [CLAUDE, GPT]
    assert _keys(client, admin_token, "enabled=false") == [OLD]
    # On one proxy, a disabled deployment counts as not enabled.
    assert _keys(client, admin_token, "proxy=local-eu1&enabled=true") == []
    assert _keys(client, admin_token, "proxy=local-eu1&enabled=false") == [GPT]
    assert _keys(client, admin_token, "proxy=local-us1&enabled=false") == []


def test_models_supported_filter(client, admin_token, deployed):
    # A null result matches neither value, so the Claude model is in no list.
    assert _keys(client, admin_token, "supported=true") == [GPT]
    assert _keys(client, admin_token, "supported=false") == [GPT]
    assert _keys(client, admin_token, "proxy=local-us1&supported=true") == [GPT]
    assert _keys(client, admin_token, "proxy=local-us1&supported=false") == []
    assert _keys(client, admin_token, "proxy=local-eu1&supported=false") == [GPT]


def test_models_priced_filter(client, admin_token, deployed, registry_db):
    assert _keys(client, admin_token, "priced=true") == [GPT]
    assert _keys(client, admin_token, "priced=false") == [CLAUDE, OLD]

    # A price row alone is enough, with no headline price.
    registry_db.add(DBRegistryModelPrice(
        model_id=model(registry_db, "openai", "gpt-old").id, scope_kind="base", scope="",
        prices={"input_cost_per_token": 1e-06}, source="manual", last_seen=TODAY,
    ))
    registry_db.commit()
    assert _keys(client, admin_token, "priced=true") == [GPT, OLD]
    assert _keys(client, admin_token, "priced=false") == [CLAUDE]


def test_models_header_counts_the_filtered_total(client, admin_token, deployed):
    response = client.get("/registry/models?enabled=true&limit=1", headers=auth(admin_token))

    assert len(response.json()) == 1
    assert response.headers["X-Total-Count"] == "2"
