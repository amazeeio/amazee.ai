from datetime import date
from decimal import Decimal

import pytest

from app.main import _OPENAPI_TIERS, _scope_schema, app
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider, DBRegistryRun

RUN_FIELDS = {"id", "step", "started_at", "finished_at", "status", "stats", "error"}
TODAY = date(2026, 1, 1)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def runs(registry_db):
    registry_db.add_all([
        DBRegistryRun(step="plugin:deepinfra_api", status="ok", stats={"models": 3}),
        DBRegistryRun(step="litellm_list", status="failed", error="proxy timed out"),
        DBRegistryRun(step="plugin:deepinfra_api", status="running"),
    ])
    registry_db.commit()


@pytest.fixture
def models(registry_db):
    openai = DBRegistryProvider(name="openai")
    bedrock = DBRegistryProvider(name="bedrock")
    registry_db.add_all([openai, bedrock])
    registry_db.flush()
    common = {"source": "litellm", "first_seen": TODAY, "last_seen": TODAY}
    gpt = DBRegistryModel(
        provider_id=openai.id,
        model_id="gpt-4o",
        mode="chat",
        input_cost_per_token=Decimal("0.0000025"),
        output_cost_per_token=Decimal("0.00001"),
        prices={"input_cost_per_token": 2.5e-06},
        **common,
    )
    registry_db.add_all([
        gpt,
        DBRegistryModel(provider_id=openai.id, model_id="gpt-old", status="removed", **common),
        DBRegistryModel(provider_id=bedrock.id, model_id="anthropic.claude-x-v1:0", **common),
    ])
    registry_db.flush()
    # The geo row goes in first, so the response order comes from the query.
    registry_db.add_all([
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="geo", scope="eu", prices={"input_cost_per_token": 2.75e-06},
            source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="base", scope="", prices={"input_cost_per_token": 2.5e-06},
            source="litellm", last_seen=TODAY,
        ),
    ])
    registry_db.commit()


def _model_ids(response):
    assert response.status_code == 200, response.text
    return [(m["provider"], m["model_id"]) for m in response.json()]


def test_runs_are_newest_first_with_all_fields(client, admin_token, runs):
    response = client.get("/registry/runs", headers=_auth(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["id"] for r in body] == [3, 2, 1]
    assert all(set(r) == RUN_FIELDS for r in body)
    assert body[1]["error"] == "proxy timed out"
    assert body[2]["stats"] == {"models": 3}


def test_runs_step_filter(client, admin_token, runs):
    response = client.get("/registry/runs?step=plugin:deepinfra_api", headers=_auth(admin_token))

    assert response.status_code == 200, response.text
    assert [(r["id"], r["step"]) for r in response.json()] == [
        (3, "plugin:deepinfra_api"),
        (1, "plugin:deepinfra_api"),
    ]


def test_runs_limit_is_enforced(client, admin_token, runs):
    assert len(client.get("/registry/runs?limit=1", headers=_auth(admin_token)).json()) == 1
    assert client.get("/registry/runs?limit=501", headers=_auth(admin_token)).status_code == 422
    assert client.get("/registry/runs?limit=0", headers=_auth(admin_token)).status_code == 422


def test_models_return_fields_and_price_scopes(client, admin_token, models):
    response = client.get("/registry/models", headers=_auth(admin_token))

    assert _model_ids(response) == [
        ("bedrock", "anthropic.claude-x-v1:0"),
        ("openai", "gpt-4o"),
        ("openai", "gpt-old"),
    ]
    claude, gpt, old = response.json()
    assert gpt["input_cost_per_token"] == 2.5e-06
    assert gpt["output_cost_per_token"] == 1e-05
    for model in (claude, gpt, old):
        for field in ("input_cost_per_token", "output_cost_per_token"):
            assert model[field] is None or isinstance(model[field], float)
    assert gpt["mode"] == "chat"
    assert gpt["first_seen"] == gpt["last_seen"] == "2026-01-01"
    assert gpt["price_scopes"] == [
        {"scope_kind": "base", "scope": "", "prices": {"input_cost_per_token": 2.5e-06}, "source": "litellm"},
        {"scope_kind": "geo", "scope": "eu", "prices": {"input_cost_per_token": 2.75e-06}, "source": "litellm"},
    ]
    assert claude["price_scopes"] == []


def test_models_filters(client, admin_token, models):
    def ids(query):
        return _model_ids(client.get(f"/registry/models?{query}", headers=_auth(admin_token)))

    assert ids("provider=openai") == [("openai", "gpt-4o"), ("openai", "gpt-old")]
    assert ids("status=removed") == [("openai", "gpt-old")]
    assert ids("q=GPT") == [("openai", "gpt-4o"), ("openai", "gpt-old")]
    # An unescaped % would match every model.
    assert ids("q=%25") == []


def test_models_limit_and_offset(client, admin_token, models):
    response = client.get("/registry/models?limit=1&offset=1", headers=_auth(admin_token))

    assert _model_ids(response) == [("openai", "gpt-4o")]
    assert client.get("/registry/models?limit=1001", headers=_auth(admin_token)).status_code == 422


@pytest.mark.parametrize("path", ["/registry/runs", "/registry/models"])
def test_registry_endpoints_need_a_system_admin(client, test_token, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=_auth(test_token)).status_code == 403


def test_registry_endpoints_are_admin_only_in_openapi():
    full = app.openapi()
    paths = ["/registry/runs", "/registry/models"]

    for path in paths:
        assert _OPENAPI_TIERS[full["paths"][path]["get"]["operationId"]] == "admin"
    assert not set(paths) & set(_scope_schema(full, 1)["paths"])
    assert set(paths) <= set(_scope_schema(full, 2)["paths"])
