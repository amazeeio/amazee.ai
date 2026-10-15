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
from tests.registry.api.conftest import CHECKED_AT, auth, model, ok
from tests.registry.conftest import add_model

ROLLOUTS = "/registry/rollouts"
UNSUPPORTED = "the proxy's LiteLLM version does not support the model"
BOTH = {"local-us1": "cred-us", "local-eu1": None}


def _support(model_id, region_id, supported=True):
    return DBRegistryModelSupport(
        model_id=model_id, region_id=region_id, priced=True, supported=supported, region_available=True,
        checked_at=CHECKED_AT,
    )


@pytest.fixture
def setup(registry_db, deployed):
    """Two access groups, gpt-4o on the US proxy in `beta`, and support rows for gpt-old on both proxies."""
    old = model(registry_db, "openai", "gpt-old")
    alpha, beta = DBRegistryAccessGroup(slug="alpha", label="Alpha"), DBRegistryAccessGroup(slug="beta", label="Beta")
    registry_db.add_all([alpha, beta, _support(old.id, deployed["us"].id), _support(old.id, deployed["eu"].id)])
    registry_db.flush()
    us_gpt = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    registry_db.add(DBRegistryModelRegionGroup(model_region_id=us_gpt.id, access_group_id=beta.id))
    for proxy in registry_db.query(DBRegistryProxy):
        proxy.imported_at = CHECKED_AT
    registry_db.commit()
    return deployed


def _allow_gpt_on_eu(db, region_id):
    """The EU proxy's support row for gpt-4o says no; make it say yes."""
    db.query(DBRegistryModelSupport).filter_by(
        model_id=model(db, "openai", "gpt-4o").id, region_id=region_id
    ).one().supported = True
    db.commit()


def _post(client, token, body, query=""):
    return client.post(f"{ROLLOUTS}{query}", json=body, headers=auth(token))


def _state(db):
    db.expire_all()
    deployments = [
        (d.id, d.model_id, d.region_id, d.model_name, d.litellm_model, d.enabled, d.litellm_params)
        for d in db.query(DBRegistryModelRegion).order_by(DBRegistryModelRegion.id)
    ]
    links = sorted((link.model_region_id, link.access_group_id) for link in db.query(DBRegistryModelRegionGroup))
    return deployments, links


def _entry(proxy, key, action, name, backend, problems=()):
    return {
        "proxy": proxy, "model": key, "action": action, "model_name": name, "litellm_model": backend,
        "problems": list(problems),
    }


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


def test_rollout_is_all_or_nothing(client, admin_token, registry_db, setup):
    before = _state(registry_db)
    body = {"models": ["openai/gpt-old", "openai/gpt-4o"], "proxies": BOTH, "groups": ["alpha"]}

    response = _post(client, admin_token, body)

    assert response.status_code == 422
    # gpt-4o is disabled on the EU proxy, whose LiteLLM version does not support it.
    assert response.json()["detail"] == [
        _entry("local-us1", "openai/gpt-old", "created", "gpt-old", "openai/gpt-old"),
        _entry("local-eu1", "openai/gpt-old", "created", "gpt-old", "openai/gpt-old"),
        _entry("local-us1", "openai/gpt-4o", "updated", "gpt-4o", "openai/gpt-4o"),
        _entry("local-eu1", "openai/gpt-4o", "updated", "gpt-4o", "openai/gpt-4o", [UNSUPPORTED]),
    ]
    assert _state(registry_db) == before

    assert len(ok(_post(client, admin_token, body, "?force=true"))) == 4
    assert _groups(client, admin_token, "openai/gpt-4o")[("local-eu1", "gpt-4o")] == (True, ["alpha"])


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_rollout_refuses_a_model_name_that_is_an_alias(client, admin_token, registry_db, setup, query):
    registry_db.add(DBRegistryAlias(region_id=setup["us"].id, alias="gpt-old", target="gpt-4o"))
    registry_db.commit()

    response = _post(client, admin_token, {"models": ["openai/gpt-old"], "proxies": BOTH}, query)

    assert response.status_code == 422
    assert [entry["problems"] for entry in response.json()["detail"]] == [
        ["the model name gpt-old is an alias on this proxy"], [],
    ]


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
        {"models": ["openai/gpt-old"], "proxies": {"local-us1": "bad name"}},
        {"models": ["openai/gpt-old"], "proxies": BOTH, "foo": 1},
    ],
)
def test_rollout_refuses_a_bad_body(client, admin_token, registry_db, setup, bare_region, body):
    before = _state(registry_db)

    assert _post(client, admin_token, body).status_code == 422
    assert _state(registry_db) == before


def test_rollout_dry_run_returns_the_entries_and_saves_nothing(client, admin_token, registry_db, setup):
    _allow_gpt_on_eu(registry_db, setup["eu"].id)
    before = _state(registry_db)
    body = {"models": ["openai/gpt-old", "openai/gpt-4o"], "proxies": BOTH, "groups": ["alpha"]}

    dry = ok(_post(client, admin_token, body, "?dry_run=true"))

    assert _state(registry_db) == before
    assert ok(_post(client, admin_token, body)) == dry


def test_rollout_query_count_does_not_grow_with_the_pairs(client, admin_token, registry_db, setup):
    for model_id in ("gpt-a", "gpt-b", "gpt-c"):
        add_model(registry_db, "openai", model_id)
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
        one = run({"models": ["openai/gpt-a"], "proxies": {"local-us1": None}, "groups": ["alpha"]})
        many = run({"models": ["openai/gpt-b", "openai/gpt-c", "openai/gpt-old"], "proxies": BOTH, "groups": ["alpha"]})
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
    assert response.json()["detail"] == "Import the proxy first: local-eu1"
    assert _state(registry_db) == before
