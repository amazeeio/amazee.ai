from datetime import UTC, datetime, timedelta

import pytest

from app.main import _OPENAPI_TIERS, _scope_schema, app
from app.registry.api.routes import router
from app.registry.importer import STEP as IMPORT_STEP
from app.registry.models import DBRegistryEvent, DBRegistryRun
from tests.registry.api.conftest import CHECKED_AT, MODEL_FIELDS, RUN_FIELDS, auth, model


@pytest.fixture
def runs(registry_db):
    registry_db.add_all([
        DBRegistryRun(step="plugin:deepinfra_api", status="ok", stats={"models": 3}),
        DBRegistryRun(step="litellm_list", status="failed", error="proxy timed out"),
        DBRegistryRun(step="plugin:deepinfra_api", status="running"),
    ])
    registry_db.commit()


def _model_ids(response):
    assert response.status_code == 200, response.text
    return [(m["provider"], m["model_id"]) for m in response.json()]


def test_runs_are_newest_first_with_all_fields(client, admin_token, runs):
    response = client.get("/registry/runs", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["id"] for r in body] == [3, 2, 1]
    assert all(set(r) == RUN_FIELDS for r in body)
    assert body[1]["error"] == "proxy timed out"
    assert body[2]["stats"] == {"models": 3}
    # litellm_list has only a failed run; the plugin step ran ok just now.
    assert [r["stale"] for r in body] == [False, True, False]


def test_runs_step_filter(client, admin_token, runs):
    response = client.get("/registry/runs?step=plugin:deepinfra_api", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert [(r["id"], r["step"]) for r in response.json()] == [
        (3, "plugin:deepinfra_api"),
        (1, "plugin:deepinfra_api"),
    ]


def test_runs_limit_is_enforced(client, admin_token, runs):
    newest = client.get("/registry/runs?limit=1", headers=auth(admin_token)).json()
    assert [r["id"] for r in newest] == [3]
    assert client.get("/registry/runs?limit=501", headers=auth(admin_token)).status_code == 422
    assert client.get("/registry/runs?limit=0", headers=auth(admin_token)).status_code == 422


def test_models_return_fields_and_price_scopes(client, admin_token, models):
    response = client.get("/registry/models", headers=auth(admin_token))

    assert _model_ids(response) == [
        ("bedrock", "anthropic.claude-x-v1:0"),
        ("openai", "gpt-4o"),
        ("openai", "gpt-old"),
    ]
    claude, gpt, old = response.json()
    assert gpt["input_cost_per_token"] == 2.5e-06
    assert gpt["output_cost_per_token"] == 1e-05
    assert set(gpt) == MODEL_FIELDS
    assert gpt["status"] == "active"
    assert old["status"] == "removed"
    assert gpt["prices"] == {"input_cost_per_token": 2.5e-06}
    assert gpt["mode"] == "chat"
    assert gpt["first_seen"] == gpt["last_seen"] == "2026-01-01"
    assert gpt["price_scopes"] == [
        {"scope_kind": "base", "scope": "", "prices": {"input_cost_per_token": 2.5e-06}, "source": "litellm"},
        {"scope_kind": "geo", "scope": "eu", "prices": {"input_cost_per_token": 2.75e-06}, "source": "litellm"},
    ]
    assert claude["price_scopes"] == []
    assert claude["family"] == "claudex" and claude["manufacturer"] == "anthropic"
    assert gpt["family"] is None and gpt["manufacturer"] is None
    assert gpt["override_eol"] is None and gpt["blocked"] is False


def test_models_filters(client, admin_token, models):
    def ids(query):
        return _model_ids(client.get(f"/registry/models?{query}", headers=auth(admin_token)))

    assert ids("provider=openai") == [("openai", "gpt-4o"), ("openai", "gpt-old")]
    assert ids("status=removed") == [("openai", "gpt-old")]
    assert ids("q=GPT") == [("openai", "gpt-4o"), ("openai", "gpt-old")]
    # An unescaped % or _ would match every model.
    assert ids("q=%25") == []
    assert ids("q=_") == []


def test_models_limit_and_offset(client, admin_token, models):
    response = client.get("/registry/models?limit=1&offset=1", headers=auth(admin_token))

    assert _model_ids(response) == [("openai", "gpt-4o")]
    assert client.get("/registry/models?limit=1001", headers=auth(admin_token)).status_code == 422
    assert client.get("/registry/models?offset=-1", headers=auth(admin_token)).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/registry/runs",
        "/registry/models",
        "/registry/models/new",
        "/registry/models/eol",
        "/registry/models/openai/gpt-4o",
        "/registry/providers",
        "/registry/proxies",
        "/registry/proxies/local-us1",
        "/registry/events",
    ],
)
def test_registry_endpoints_need_a_system_admin(client, test_token, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth(test_token)).status_code == 403


def test_registry_endpoints_are_admin_only_in_openapi():
    full = app.openapi()
    # Every registry route, so a new one cannot be left out of this check.
    paths = [route.path_format for route in router.routes]
    # path_format drops the converter, as the OpenAPI document does.
    assert "/registry/models/{key}" in paths

    for route in router.routes:
        for method in route.methods:
            operation = full["paths"][route.path_format][method.lower()]
            assert _OPENAPI_TIERS[operation["operationId"]] == "admin"
    assert not set(paths) & set(_scope_schema(full, 1)["paths"])
    assert set(paths) <= set(_scope_schema(full, 2)["paths"])


EVENT_FIELDS = {"key", "proxy", "type", "created_at", "data"}


@pytest.fixture
def events(registry_db, deployed):
    gpt = model(registry_db, "openai", "gpt-4o")
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    old = model(registry_db, "openai", "gpt-old")
    later = CHECKED_AT + timedelta(hours=1)
    registry_db.add_all([
        DBRegistryEvent(type="model.discovered", model_id=gpt.id, created_at=CHECKED_AT, data={}),
        DBRegistryEvent(
            type="model.support_changed", model_id=gpt.id, region_id=deployed["us"].id, created_at=later,
            data={"supported": [None, True]},
        ),
        # Same time as the one before it, so the id decides the order.
        DBRegistryEvent(
            type="model.eol_announced", model_id=claude.id, created_at=later, data={"eol_date": "2027-01-01"},
        ),
        DBRegistryEvent(
            type="model.discovered", model_id=old.id, created_at=CHECKED_AT - timedelta(days=1), data={},
        ),
    ])
    registry_db.commit()


def _events(client, token, **params):
    response = client.get("/registry/events", params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    return response


def test_events_newest_first_with_all_fields(client, admin_token, events):
    response = _events(client, admin_token)

    assert response.headers["X-Total-Count"] == "4"
    body = response.json()
    assert all(set(e) == EVENT_FIELDS for e in body)
    assert [(e["key"], e["type"], e["proxy"]) for e in body] == [
        ("bedrock/anthropic.claude-x-v1:0", "model.eol_announced", None),
        ("openai/gpt-4o", "model.support_changed", "local-us1"),
        ("openai/gpt-4o", "model.discovered", None),
        ("openai/gpt-old", "model.discovered", None),
    ]
    assert body[0]["data"] == {"eol_date": "2027-01-01"}
    assert body[0]["created_at"] == "2026-01-02T04:04:05Z"


def test_events_filters(client, admin_token, events):
    def keys(**params):
        return [e["key"] for e in _events(client, admin_token, **params).json()]

    assert keys(type="model.discovered") == ["openai/gpt-4o", "openai/gpt-old"]
    assert keys(since=CHECKED_AT.isoformat()) == [
        "bedrock/anthropic.claude-x-v1:0", "openai/gpt-4o", "openai/gpt-4o",
    ]
    assert keys(type="model.discovered", since=CHECKED_AT.isoformat()) == ["openai/gpt-4o"]
    assert keys(type="model.nope") == []


def test_events_limit_and_offset(client, admin_token, events):
    response = _events(client, admin_token, limit=2, offset=1)

    assert [e["type"] for e in response.json()] == ["model.support_changed", "model.discovered"]
    assert response.headers["X-Total-Count"] == "4"
    assert client.get("/registry/events?limit=1001", headers=auth(admin_token)).status_code == 422
    assert client.get("/registry/events?offset=-1", headers=auth(admin_token)).status_code == 422


def test_runs_stale_flag(client, admin_token, registry_db):
    now = datetime.now(UTC)
    registry_db.add_all([
        # Only an old ok run, then a failure: every row of the step is stale.
        DBRegistryRun(step="history", status="ok", started_at=now - timedelta(days=3)),
        DBRegistryRun(step="history", status="failed", started_at=now - timedelta(hours=1)),
        # A recent ok run keeps the step fresh, whatever ran after it.
        DBRegistryRun(step="support", status="ok", started_at=now - timedelta(hours=47)),
        DBRegistryRun(step="support", status="failed", started_at=now - timedelta(hours=2)),
        DBRegistryRun(step="support", status="running", started_at=now),
        # A step that never ran ok is stale.
        DBRegistryRun(step="litellm_list", status="failed", error="proxy timed out"),
    ])
    registry_db.commit()

    response = client.get("/registry/runs", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert [(r["step"], r["status"], r["stale"]) for r in response.json()] == [
        ("litellm_list", "failed", True),
        ("support", "running", False),
        ("support", "failed", False),
        ("support", "ok", False),
        ("history", "failed", True),
        ("history", "ok", True),
    ]


def test_runs_proxy_import_is_never_stale(client, admin_token, registry_db):
    old = datetime.now(UTC) - timedelta(days=30)
    registry_db.add_all([
        DBRegistryRun(step=IMPORT_STEP, status="ok", started_at=old),
        DBRegistryRun(step="history", status="ok", started_at=old),
    ])
    registry_db.commit()

    response = client.get("/registry/runs", headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert [(r["step"], r["stale"]) for r in response.json()] == [("history", True), (IMPORT_STEP, False)]


def test_runs_total_count_header(client, admin_token, runs):
    response = client.get("/registry/runs?limit=1", headers=auth(admin_token))
    assert len(response.json()) == 1
    assert response.headers["X-Total-Count"] == "3"

    filtered = client.get("/registry/runs?step=plugin:deepinfra_api&limit=1", headers=auth(admin_token))
    assert filtered.headers["X-Total-Count"] == "2"
