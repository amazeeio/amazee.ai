from datetime import date, timedelta

import pytest

from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryCloudAvailability,
    DBRegistryModel,
    DBRegistryModelHistory,
    DBRegistryModelLifecycle,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
)
from tests.registry.api.conftest import CHECKED_AT, MODEL_FIELDS, TODAY, auth, model


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


def _set(db, key, **fields):
    row = model(db, *key.split("/", 1))
    for name, value in fields.items():
        setattr(row, name, value)
    db.commit()


def test_new_models_window_and_order(client, admin_token, deployed, registry_db):
    today = date.today()
    _set(registry_db, GPT, first_seen=today)
    _set(registry_db, OLD, first_seen=today)
    _set(registry_db, CLAUDE, first_seen=today - timedelta(days=30))

    response = client.get("/registry/models/new", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    body = response.json()
    # Newest first, then provider name and model id.
    assert [f"{m['provider']}/{m['model_id']}" for m in body] == [GPT, OLD, CLAUDE]
    assert set(body[0]) == MODEL_FIELDS
    assert [p["proxy"] for p in body[0]["proxies"]] == ["local-eu1", "local-us1"]

    assert client.get("/registry/models/new?days=29", headers=auth(admin_token)).headers["X-Total-Count"] == "2"
    assert _keys_at(client, admin_token, "/registry/models/new?limit=1&offset=2") == [CLAUDE]


def test_new_models_skip_older_models(client, admin_token, models, registry_db):
    for key in (GPT, OLD, CLAUDE):
        _set(registry_db, key, first_seen=date.today() - timedelta(days=31))

    assert _keys_at(client, admin_token, "/registry/models/new") == []


def test_eol_models_window_and_order(client, admin_token, deployed, registry_db):
    today = date.today()
    _set(registry_db, GPT, eol_date=today - timedelta(days=7))
    _set(registry_db, CLAUDE, eol_date=today + timedelta(days=90))
    _set(registry_db, OLD, status="deprecated", eol_date=None)

    response = client.get("/registry/models/eol", headers=auth(admin_token))
    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    body = response.json()
    # By EOL date, and a deprecated model with no date comes last.
    assert [f"{m['provider']}/{m['model_id']}" for m in body] == [GPT, CLAUDE, OLD]
    assert set(body[0]) == MODEL_FIELDS
    assert [p["proxy"] for p in body[1]["proxies"]] == ["local-us1"]

    assert _keys_at(client, admin_token, "/registry/models/eol?days=89") == [GPT, OLD]


def test_eol_models_skip_models_outside_the_window(client, admin_token, models, registry_db):
    today = date.today()
    _set(registry_db, GPT, eol_date=today - timedelta(days=8))
    _set(registry_db, CLAUDE, eol_date=today + timedelta(days=91), status="deprecated")
    # A removed model with no date is not in the list.
    _set(registry_db, OLD, status="removed", eol_date=None)

    assert _keys_at(client, admin_token, "/registry/models/eol") == []
    assert _keys_at(client, admin_token, "/registry/models/eol?days=91") == [CLAUDE]


@pytest.mark.parametrize("path", ["/registry/models/new", "/registry/models/eol"])
@pytest.mark.parametrize("days", [0, 366])
def test_new_and_eol_days_range(client, admin_token, path, days):
    assert client.get(f"{path}?days={days}", headers=auth(admin_token)).status_code == 422


def _keys_at(client, token, path):
    response = client.get(path, headers=auth(token))
    assert response.status_code == 200, response.text
    return [f"{m['provider']}/{m['model_id']}" for m in response.json()]


DETAIL_FIELDS = MODEL_FIELDS | {"supports", "lifecycle", "availability", "deployments", "history"}


def test_model_detail(client, admin_token, deployed, registry_db):
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    claude.supports = ["vision"]
    deployment = registry_db.query(DBRegistryModelRegion).filter_by(model_id=claude.id).one()
    late, early = DBRegistryAccessGroup(slug="team-b"), DBRegistryAccessGroup(slug="team-a")
    registry_db.add_all([late, early])
    registry_db.flush()
    registry_db.add_all([
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=late.id),
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=early.id),
        DBRegistryModelPrice(
            model_id=claude.id, scope_kind="base", scope="", prices={"input_cost_per_token": 3e-06},
            source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelPrice(
            model_id=claude.id, scope_kind="cloud_region", scope="us-east-1",
            prices={"input_cost_per_token": 3.1e-06}, source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelPrice(
            model_id=claude.id, scope_kind="geo", scope="us", prices={"input_cost_per_token": 3.3e-06},
            source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelLifecycle(
            provider="bedrock", model_id=claude.model_id, source="litellm", eol_date=TODAY, last_seen=TODAY,
        ),
        DBRegistryModelLifecycle(
            provider="bedrock", model_id=claude.model_id, source="bedrock_api", status="LEGACY",
            legacy_at=TODAY, last_seen=TODAY,
        ),
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=claude.model_id, cloud_region="us-east-1", source="bedrock_api",
            call_types=["INFERENCE_PROFILE"], last_seen=TODAY,
        ),
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=claude.model_id, cloud_region="eu-west-1", source="bedrock_api",
            call_types=["ON_DEMAND"], last_seen=TODAY,
        ),
        DBRegistryModelHistory(model_id=claude.id, recorded_at=CHECKED_AT, state={"status": "active"}),
        DBRegistryModelHistory(
            model_id=claude.id, recorded_at=CHECKED_AT + timedelta(days=1), state={"status": "deprecated"},
        ),
    ])
    registry_db.commit()

    response = client.get("/registry/models/bedrock/anthropic.claude-x-v1:0", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == DETAIL_FIELDS
    assert body["model_id"] == "anthropic.claude-x-v1:0"
    assert body["proxies"] == [_proxy("local-us1", priced=True, checked_at=CHECKED_AT, enabled=True)]
    assert body["supports"] == ["vision"]
    assert [(r["source"], r["status"]) for r in body["lifecycle"]] == [("bedrock_api", "LEGACY"), ("litellm", None)]
    assert [(r["cloud_region"], r["call_types"]) for r in body["availability"]] == [
        ("eu-west-1", ["ON_DEMAND"]),
        ("us-east-1", ["INFERENCE_PROFILE"]),
    ]
    assert body["deployments"] == [{
        "proxy": "local-us1",
        "model_name": "claude-x",
        "litellm_model": "bedrock/us.anthropic.claude-x-v1:0",
        "enabled": True,
        "input_cost_per_token": None,
        "output_cost_per_token": None,
        "prices": {},
        "access_groups": ["team-a", "team-b"],
        # The deployed string has the `us.` geo prefix, so the geo price beats the cloud region price.
        "price": {
            "scope_kind": "geo", "scope": "us", "prices": {"input_cost_per_token": 3.3e-06}, "source": "litellm",
        },
    }]
    assert [h["state"] for h in body["history"]] == [{"status": "deprecated"}, {"status": "active"}]


def test_model_detail_deployment_gets_the_proxy_cloud_region_price(client, admin_token, deployed, registry_db):
    gpt = model(registry_db, "openai", "gpt-4o")
    registry_db.get(DBRegistryProxy, deployed["us"].id).cloud_regions = {"openai": "eastus"}
    registry_db.add(DBRegistryModelPrice(
        model_id=gpt.id, scope_kind="cloud_region", scope="eastus", prices={"input_cost_per_token": 2.6e-06},
        source="litellm", last_seen=TODAY,
    ))
    registry_db.commit()

    response = client.get("/registry/models/openai/gpt-4o", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    prices = {d["proxy"]: (d["enabled"], d["price"]["scope_kind"], d["price"]["scope"]) for d in response.json()["deployments"]}
    # The EU proxy has no cloud region for openai, so it gets the base price.
    assert prices == {"local-eu1": (False, "base", ""), "local-us1": (True, "cloud_region", "eastus")}


def test_model_detail_splits_the_key_on_the_first_slash(client, admin_token, registry_db):
    deepinfra = DBRegistryProvider(name="deepinfra")
    registry_db.add(deepinfra)
    registry_db.flush()
    registry_db.add(DBRegistryModel(
        provider_id=deepinfra.id, model_id="BAAI/bge-m3", source="litellm", first_seen=TODAY, last_seen=TODAY,
    ))
    registry_db.commit()

    response = client.get("/registry/models/deepinfra/BAAI/bge-m3", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["provider"], body["model_id"]) == ("deepinfra", "BAAI/bge-m3")
    assert body["deployments"] == body["history"] == body["lifecycle"] == []


@pytest.mark.parametrize("key", ["openai/nope", "gpt-4o", "nope/gpt-4o"])
def test_model_detail_unknown_key_is_404(client, admin_token, models, key):
    response = client.get(f"/registry/models/{key}", headers=auth(admin_token))

    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found"


def test_model_routes_new_and_eol_are_not_model_keys(client, admin_token, models, registry_db):
    provider = DBRegistryProvider(name="new")
    registry_db.add(provider)
    registry_db.flush()
    registry_db.add(DBRegistryModel(
        provider_id=provider.id, model_id="x", source="litellm", first_seen=TODAY, last_seen=TODAY,
    ))
    registry_db.commit()

    for path in ("/registry/models/new", "/registry/models/eol"):
        response = client.get(path, headers=auth(admin_token))
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)
    detail = client.get("/registry/models/new/x", headers=auth(admin_token))
    assert detail.status_code == 200, detail.text
    assert (detail.json()["provider"], detail.json()["model_id"]) == ("new", "x")
