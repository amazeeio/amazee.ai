import pytest
from sqlalchemy import event

from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProxy,
)
from tests.registry.api.conftest import UNSUPPORTED, auth, messages, model, ok, support_row
from tests.registry.api.conftest import deployment_state as _state
from tests.registry.conftest import add_model

ROLLOUTS = "/registry/rollouts"
BOTH = {"local-us1": "cred-us", "local-eu1": None}


@pytest.fixture
def setup(registry_db, writable):
    """gpt-4o on the US proxy carries `beta`."""
    beta = registry_db.query(DBRegistryAccessGroup).filter_by(slug="beta").one()
    us_gpt = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    registry_db.add(DBRegistryModelRegionGroup(model_region_id=us_gpt.id, access_group_id=beta.id))
    registry_db.commit()
    return writable


def _allow_gpt_on_eu(db, region_id):
    """The EU proxy's support row for gpt-4o says no; make it say yes."""
    db.query(DBRegistryModelSupport).filter_by(
        model_id=model(db, "openai", "gpt-4o").id, region_id=region_id
    ).one().supported = True
    db.commit()


def _post(client, token, body, query=""):
    return client.post(f"{ROLLOUTS}{query}", json=body, headers=auth(token))


def _entry(proxy, key, action, name, backend):
    return {"proxy": proxy, "model": key, "action": action, "model_name": name, "litellm_model": backend}


def _groups(client, token, key):
    detail = ok(client.get(f"/registry/models/{key}", headers=auth(token)))
    return {(d["proxy"], d["model_name"]): (d["enabled"], d["access_groups"]) for d in detail["deployments"]}


def test_rollout_creates_and_updates_in_body_order(client, admin_token, registry_db, setup):
    _allow_gpt_on_eu(registry_db, setup["eu"].id)
    body = {"models": ["openai/gpt-old", "openai/gpt-4o", "openai/gpt-old"], "proxies": BOTH, "groups": ["alpha"]}

    assert ok(_post(client, admin_token, body)) == [
        _entry("local-us1", "openai/gpt-old", "created", "gpt-old", "openai/gpt-old"),
        _entry("local-eu1", "openai/gpt-old", "created", "gpt-old", "openai/gpt-old"),
        _entry("local-us1", "openai/gpt-4o", "updated", "gpt-4o", "openai/gpt-4o"),
        _entry("local-eu1", "openai/gpt-4o", "updated", "gpt-4o", "openai/gpt-4o"),
    ]

    assert _groups(client, admin_token, "openai/gpt-4o") == {
        ("local-eu1", "gpt-4o"): (True, ["alpha"]),
        ("local-us1", "gpt-4o"): (True, ["alpha", "beta"]),
    }
    assert _groups(client, admin_token, "openai/gpt-old") == {
        ("local-eu1", "gpt-old"): (True, ["alpha"]),
        ("local-us1", "gpt-old"): (True, ["alpha"]),
    }
    params = {
        region_id: params
        for region_id, params in registry_db.query(DBRegistryModelRegion.region_id, DBRegistryModelRegion.litellm_params)
        .filter_by(model_id=model(registry_db, "openai", "gpt-old").id)
    }
    assert params == {setup["us"].id: {"litellm_credential_name": "cred-us"}, setup["eu"].id: {}}


def test_rollout_accepts_a_credential_name_with_a_space(client, admin_token, registry_db, setup):
    ok(_post(client, admin_token, {"models": ["openai/gpt-old"], "proxies": {"local-us1": "AWS Bedrock"}}))

    deployment = registry_db.query(DBRegistryModelRegion).filter_by(
        model_id=model(registry_db, "openai", "gpt-old").id
    ).one()
    assert deployment.litellm_params == {"litellm_credential_name": "AWS Bedrock"}


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_rollout_refuses_a_default_name_that_another_model_uses(client, admin_token, registry_db, setup, query):
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    registry_db.add(DBRegistryModelRegion(
        model_id=claude.id, region_id=setup["us"].id, model_name="gpt-old", litellm_model="bedrock/x",
    ))
    registry_db.commit()
    before = _state(registry_db)

    response = _post(client, admin_token, {"models": ["openai/gpt-old"], "proxies": BOTH}, query)

    assert response.status_code == 422
    assert response.json()["problems"] == [{
        "message": "the model name gpt-old is used by another model on this proxy",
        "proxy": "local-us1", "model": "openai/gpt-old",
    }]
    assert _state(registry_db) == before


def test_rollout_refuses_two_new_models_under_one_name(client, admin_token, registry_db, setup):
    add_model(registry_db, "bedrock", "gpt-old")
    registry_db.commit()

    body = {"models": ["openai/gpt-old", "bedrock/gpt-old"], "proxies": {"local-eu1": None}}
    response = _post(client, admin_token, body, "?force=true")

    assert response.status_code == 422
    assert response.json()["problems"] == [{
        "message": "the model name gpt-old is used by another model on this proxy",
        "proxy": "local-eu1", "model": "bedrock/gpt-old",
    }]


def test_rollout_updates_two_deployments_of_one_model_under_one_name(client, admin_token, registry_db, setup):
    gpt = model(registry_db, "openai", "gpt-4o")
    registry_db.add(DBRegistryModelRegion(
        model_id=gpt.id, region_id=setup["us"].id, model_name="gpt-4o", litellm_model="openai/gpt-4o",
    ))
    registry_db.commit()

    assert ok(_post(client, admin_token, {"models": ["openai/gpt-4o"], "proxies": {"local-us1": None}})) == [
        _entry("local-us1", "openai/gpt-4o", "updated", "gpt-4o", "openai/gpt-4o"),
    ]


def test_rollout_is_all_or_nothing(client, admin_token, registry_db, setup):
    before = _state(registry_db)
    body = {"models": ["openai/gpt-old", "openai/gpt-4o"], "proxies": BOTH, "groups": ["alpha"]}

    response = _post(client, admin_token, body)

    assert response.status_code == 422
    # gpt-4o is disabled on the EU proxy, whose LiteLLM version does not support it.
    assert response.json()["problems"] == [{"message": UNSUPPORTED, "proxy": "local-eu1", "model": "openai/gpt-4o"}]
    assert _state(registry_db) == before

    assert len(ok(_post(client, admin_token, body, "?force=true"))) == 4
    assert _groups(client, admin_token, "openai/gpt-4o")[("local-eu1", "gpt-4o")] == (True, ["alpha"])


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_rollout_refuses_a_model_name_that_is_an_alias(client, admin_token, registry_db, setup, query):
    registry_db.add(DBRegistryAlias(region_id=setup["us"].id, alias="gpt-old", target="gpt-4o"))
    registry_db.commit()

    response = _post(client, admin_token, {"models": ["openai/gpt-old"], "proxies": BOTH}, query)

    assert response.status_code == 422
    assert response.json()["problems"] == [{
        "message": "the model name gpt-old is an alias on this proxy", "proxy": "local-us1", "model": "openai/gpt-old",
    }]


@pytest.mark.parametrize(
    "body",
    [
        {"models": ["openai/nope"], "proxies": BOTH},
        {"models": ["nope"], "proxies": BOTH},
        {"models": ["openai/gpt-old"], "proxies": {"nope": None}},
        {"models": ["openai/gpt-old"], "proxies": {"local-ap1": None}},
        {"models": ["openai/gpt-old"], "proxies": BOTH, "groups": ["nope"]},
        {"models": [], "proxies": BOTH},
        {"models": ["openai/gpt-old"], "proxies": {}},
        {"models": ["openai/gpt-old"], "proxies": {"local-us1": "bad/name"}},
        {"models": ["openai/gpt-old"], "proxies": {"local-us1": " AWS"}},
        {"models": ["openai/gpt-old"], "proxies": {"local-us1": "AWS "}},
        {"models": ["openai/gpt-old"], "proxies": BOTH, "foo": 1},
    ],
)
def test_rollout_refuses_a_bad_body(client, admin_token, registry_db, setup, bare_region, body):
    before = _state(registry_db)

    assert _post(client, admin_token, body).status_code == 422
    assert _state(registry_db) == before


def test_rollout_lists_every_unknown_proxy_and_model(client, admin_token, setup, bare_region):
    body = {"models": ["openai/nope", "openai/gpt-old", "nope"], "proxies": {"nope": None, "local-ap1": None, **BOTH}}

    response = _post(client, admin_token, body)

    assert response.status_code == 422
    assert response.json()["problems"] == [
        {"message": "Unknown proxy nope", "proxy": "nope", "model": None},
        {"message": "Unknown proxy local-ap1", "proxy": "local-ap1", "model": None},
        {"message": "Unknown model openai/nope", "proxy": None, "model": "openai/nope"},
        {"message": "Unknown model nope", "proxy": None, "model": "nope"},
    ]


def test_rollout_dry_run_returns_the_entries_and_saves_nothing(client, admin_token, registry_db, setup):
    _allow_gpt_on_eu(registry_db, setup["eu"].id)
    before = _state(registry_db)
    body = {"models": ["openai/gpt-old", "openai/gpt-4o"], "proxies": BOTH, "groups": ["alpha"]}

    dry = ok(_post(client, admin_token, body, "?dry_run=true"))

    assert _state(registry_db) == before
    assert ok(_post(client, admin_token, body)) == dry


def test_rollout_query_count_does_not_grow_with_the_pairs(client, admin_token, registry_db, setup):
    models = {model_id: add_model(registry_db, "openai", model_id) for model_id in ("gpt-a", "gpt-b", "gpt-c", "gpt-d")}
    # Disabled deployments on both proxies, so the larger rollout also runs the `updated` path many times.
    registry_db.add_all(
        DBRegistryModelRegion(
            model_id=models[model_id].id, region_id=region.id, model_name=model_id, litellm_model=f"openai/{model_id}",
            litellm_deployment_id=f"{model_id}-{region.name}", enabled=False,
        )
        for model_id in ("gpt-c", "gpt-d")
        for region in setup.values()
    )
    registry_db.commit()
    statements = []

    def count(*_):
        statements.append(1)

    def run(body):
        statements.clear()
        ok(_post(client, admin_token, body, "?force=true"))
        return len(statements)

    engine = registry_db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        # One created pair and one updated pair (gpt-4o is disabled on the EU proxy).
        one = run({"models": ["openai/gpt-a", "openai/gpt-4o"], "proxies": {"local-eu1": None}, "groups": ["alpha"]})
        many = run({
            "models": ["openai/gpt-b", "openai/gpt-old", "openai/gpt-c", "openai/gpt-d"], "proxies": BOTH,
            "groups": ["alpha"],
        })
    finally:
        event.remove(engine, "before_cursor_execute", count)

    # More than zero, so the test fails when the listener sees nothing.
    assert one == many > 0


def test_rollout_to_a_proxy_that_was_never_imported_gets_409(client, admin_token, registry_db, setup):
    registry_db.get(DBRegistryProxy, setup["eu"].id).imported_at = None
    registry_db.commit()
    before = _state(registry_db)

    response = _post(client, admin_token, {"models": ["openai/gpt-old"], "proxies": BOTH})
    assert response.status_code == 409
    assert response.json()["detail"] == "Import the proxy local-eu1 first"

    # An unknown model is listed too, and its 422 outweighs the 409.
    response = _post(client, admin_token, {"models": ["openai/gpt-old", "openai/nope"], "proxies": BOTH})
    assert response.status_code == 422
    assert messages(response) == ["Import the proxy local-eu1 first", "Unknown model openai/nope"]
    assert _state(registry_db) == before


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_rollout_that_turns_on_an_imported_global_backend_gets_422(client, admin_token, registry_db, setup, query):
    row = add_model(registry_db, model_id="anthropic.new-v1:0")
    registry_db.add(DBRegistryModelRegion(
        model_id=row.id, region_id=setup["us"].id, model_name="new", litellm_model="bedrock/global.anthropic.new-v1:0",
        litellm_deployment_id="us-global", enabled=False,
    ))
    registry_db.commit()
    before = _state(registry_db)

    response = _post(client, admin_token, {"models": ["bedrock/anthropic.new-v1:0"], "proxies": {"local-us1": None}}, query)

    assert response.status_code == 422
    assert "the geo prefix global is not allowed on this proxy" in messages(response)
    assert _state(registry_db) == before


def test_rollout_turns_on_a_stored_mantle_deployment(client, admin_token, registry_db, setup):
    row = add_model(registry_db, "bedrock_mantle", "anthropic.opus")
    registry_db.add_all([
        support_row(row.id, setup["us"].id),
        # The importer stores a Mantle deployment with the backend of LiteLLM's OpenAI route.
        DBRegistryModelRegion(
            model_id=row.id, region_id=setup["us"].id, model_name="opus", litellm_model="openai/anthropic.opus",
            litellm_deployment_id="us-opus", enabled=False,
        ),
    ])
    registry_db.commit()

    body = {"models": ["bedrock_mantle/anthropic.opus"], "proxies": {"local-us1": None}}

    assert ok(_post(client, admin_token, body)) == [
        _entry("local-us1", "bedrock_mantle/anthropic.opus", "updated", "opus", "openai/anthropic.opus"),
    ]
    assert _groups(client, admin_token, "bedrock_mantle/anthropic.opus") == {("local-us1", "opus"): (True, [])}
