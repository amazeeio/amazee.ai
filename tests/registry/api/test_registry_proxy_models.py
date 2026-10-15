from datetime import date

import pytest

from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryCloudAvailability,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProxy,
)
from tests.registry.api.conftest import CHECKED_AT, auth, model, ok
from tests.registry.conftest import add_model

PROXIES = "/registry/proxies"
NEW = "anthropic.new-v1:0"
UNKNOWN = "support is unknown: the support check has no result for this proxy"
UNSUPPORTED = "the proxy's LiteLLM version does not support the model"
UNAVAILABLE = "the proxy's cloud region does not offer the model"
PROVISIONED = "the proxy's cloud region offers the model only with provisioned throughput"
NEEDS_PROFILE = "the proxy's cloud region offers the model only through a geo profile; use the us. prefix"
BLOCKED = "the model is blocked"
FUTURE = date(2999, 1, 1)
PAST = date(2000, 1, 1)


@pytest.fixture
def setup(registry_db, deployed):
    """Two access groups, the alias `zeta` on gpt-4o and `chat` on claude-x, and a support row for gpt-old."""
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add_all([
        DBRegistryAccessGroup(slug="alpha", label="Alpha"),
        DBRegistryAccessGroup(slug="beta", label="Beta"),
        DBRegistryAlias(region_id=deployed["us"].id, alias="zeta", target="gpt-4o"),
        DBRegistryAlias(region_id=deployed["us"].id, alias="chat", target="claude-x"),
        _support(old.id, deployed["us"].id),
        _support(old.id, deployed["eu"].id),
    ])
    for proxy in registry_db.query(DBRegistryProxy):
        proxy.imported_at = CHECKED_AT
    registry_db.commit()
    return deployed


def _support(model_id, region_id, supported=True, region_available=True):
    return DBRegistryModelSupport(
        model_id=model_id, region_id=region_id, priced=True, supported=supported,
        region_available=region_available, checked_at=CHECKED_AT,
    )


def _new_bedrock_model(db, region_id, call_types=("ON_DEMAND",), support=True, **support_fields):
    """A Bedrock model with no deployment, offered in us-east-1 with the given call types."""
    row = add_model(db, model_id=NEW)
    db.add(
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=NEW, cloud_region="us-east-1",
            source="bedrock_community", call_types=list(call_types), last_seen=date(2026, 9, 27),
        )
    )
    if support:
        db.add(_support(row.id, region_id, **support_fields))
    db.commit()
    return row


def _put(client, token, proxy, key, body=None, query=""):
    return client.put(f"{PROXIES}/{proxy}/models/{key}{query}", json=body, headers=auth(token))


def _state(db):
    db.expire_all()
    deployments = [
        (d.id, d.model_id, d.region_id, d.model_name, d.litellm_model, d.litellm_deployment_id, d.enabled,
         d.litellm_params, d.model_info, d.prices)
        for d in db.query(DBRegistryModelRegion).order_by(DBRegistryModelRegion.id)
    ]
    links = sorted(
        (link.model_region_id, link.access_group_id) for link in db.query(DBRegistryModelRegionGroup)
    )
    return deployments, links


def _deployment(db, region_id, model_name):
    db.expire_all()
    return db.query(DBRegistryModelRegion).filter_by(region_id=region_id, model_name=model_name).one()


def _link(db, region_id, model_name, slug):
    group = db.query(DBRegistryAccessGroup).filter_by(slug=slug).one()
    db.add(
        DBRegistryModelRegionGroup(
            model_region_id=_deployment(db, region_id, model_name).id, access_group_id=group.id
        )
    )
    db.commit()


def test_put_with_no_body_creates_a_deployment_with_the_defaults(client, admin_token, registry_db, setup):
    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-old"), 201)

    assert body == [{
        "proxy": "local-us1", "model_name": "gpt-old", "litellm_model": "openai/gpt-old",
        "litellm_deployment_id": None, "enabled": True,
        "input_cost_per_token": None, "output_cost_per_token": None, "prices": {}, "access_groups": [],
        "price": None,
    }]
    row = _deployment(registry_db, setup["us"].id, "gpt-old")
    assert (row.litellm_deployment_id, row.litellm_params, row.model_info) == (None, {}, {})


@pytest.mark.parametrize(
    "proxy, call_types, backend",
    [
        ("local-us1", ["ON_DEMAND", "INFERENCE_PROFILE"], f"bedrock/us.{NEW}"),
        ("local-us1", ["ON_DEMAND"], f"bedrock/{NEW}"),
        # The EU proxy has no Bedrock cloud region.
        ("local-eu1", ["INFERENCE_PROFILE"], f"bedrock/{NEW}"),
    ],
)
def test_put_default_backend(client, admin_token, registry_db, setup, proxy, call_types, backend):
    region = setup["us" if proxy == "local-us1" else "eu"]
    _new_bedrock_model(registry_db, region.id, call_types)

    body = ok(_put(client, admin_token, proxy, f"bedrock/{NEW}"), 201)

    assert [(d["model_name"], d["litellm_model"]) for d in body] == [(NEW, backend)]


def test_put_with_every_field_creates_the_deployment(client, admin_token, registry_db, setup):
    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-old", {
        "model_name": "old", "litellm_model": "openai/gpt-old", "credential": "openai-main",
        "groups": ["beta", "alpha"], "enabled": True,
    }), 201)

    assert [(d["model_name"], d["access_groups"]) for d in body] == [("old", ["alpha", "beta"])]
    assert _deployment(registry_db, setup["us"].id, "old").litellm_params == {"litellm_credential_name": "openai-main"}


def test_put_changes_one_deployment(client, admin_token, registry_db, setup):
    _link(registry_db, setup["us"].id, "gpt-4o", "beta")

    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"groups": ["alpha"], "credential": "c1"}))

    assert [(d["model_name"], d["access_groups"]) for d in body] == [("gpt-4o", ["alpha"])]
    assert _deployment(registry_db, setup["us"].id, "gpt-4o").litellm_params == {"litellm_credential_name": "c1"}

    ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"credential": None}))
    row = _deployment(registry_db, setup["us"].id, "gpt-4o")
    assert row.litellm_params == {}
    # A body with no groups keeps them.
    assert len(registry_db.query(DBRegistryModelRegionGroup).filter_by(model_region_id=row.id).all()) == 1


def test_put_changes_every_deployment_of_the_model(client, admin_token, registry_db, setup):
    gpt = model(registry_db, "openai", "gpt-4o")
    registry_db.add(DBRegistryModelRegion(
        model_id=gpt.id, region_id=setup["us"].id, model_name="gpt-4o-b", litellm_model="openai/gpt-4o",
    ))
    registry_db.commit()

    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"credential": "c1", "groups": ["alpha"]}))

    assert [(d["model_name"], d["access_groups"]) for d in body] == [("gpt-4o", ["alpha"]), ("gpt-4o-b", ["alpha"])]
    for name in ("gpt-4o", "gpt-4o-b"):
        assert _deployment(registry_db, setup["us"].id, name).litellm_params == {"litellm_credential_name": "c1"}
    for field, value in (("model_name", "x"), ("litellm_model", "openai/gpt-4o")):
        assert _put(client, admin_token, "local-us1", "openai/gpt-4o", {field: value}).status_code == 422


@pytest.mark.parametrize(
    "proxy, key", [("nope", "openai/gpt-4o"), ("local-ap1", "openai/gpt-4o"), ("local-us1", "openai/nope")]
)
def test_put_not_found(client, admin_token, setup, bare_region, proxy, key):
    assert _put(client, admin_token, proxy, key).status_code == 404


@pytest.mark.parametrize(
    "body",
    [{"groups": ["nope"]}, {"credential": "bad name"}, {"credential": "x" * 129}, {"foo": 1}, {"enabled": None},
     {"model_name": None}],
)
def test_put_refuses_a_bad_body(client, admin_token, registry_db, setup, body):
    before = _state(registry_db)

    assert _put(client, admin_token, "local-us1", "openai/gpt-4o", body).status_code == 422
    assert _put(client, admin_token, "local-us1", "openai/gpt-old", body).status_code == 422
    assert _state(registry_db) == before


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_put_refuses_a_geo_prefix_the_proxy_does_not_allow(client, admin_token, registry_db, setup, query):
    _new_bedrock_model(registry_db, setup["us"].id)

    response = _put(client, admin_token, "local-us1", f"bedrock/{NEW}", {"litellm_model": f"bedrock/eu.{NEW}"}, query)

    assert response.status_code == 422
    assert response.json()["detail"] == "the geo prefix eu is not allowed on this proxy"


def test_put_refuses_a_backend_for_another_model(client, admin_token, setup):
    for key in ("openai/gpt-old", "openai/gpt-4o"):
        response = _put(client, admin_token, "local-us1", key, {"litellm_model": "openai/gpt-other"})
        assert response.status_code == 422
        assert response.json()["detail"] == "the backend is for another model"


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"support": False}, UNKNOWN),
        ({"supported": None}, UNKNOWN),
        ({"supported": False}, UNSUPPORTED),
        ({"region_available": False}, UNAVAILABLE),
        ({"call_types": ["PROVISIONED"]}, PROVISIONED),
        ({"blocked": True}, BLOCKED),
    ],
)
def test_put_enable_check(client, admin_token, registry_db, setup, kwargs, problem):
    blocked = kwargs.pop("blocked", False)
    row = _new_bedrock_model(registry_db, setup["us"].id, **kwargs)
    row.blocked = blocked
    registry_db.commit()

    response = _put(client, admin_token, "local-us1", f"bedrock/{NEW}")
    assert response.status_code == 422
    assert response.json()["detail"] == [problem]

    ok(_put(client, admin_token, "local-us1", f"bedrock/{NEW}", query="?force=true"), 201)


def test_put_refuses_an_in_region_id_that_needs_a_geo_profile(client, admin_token, registry_db, setup):
    _new_bedrock_model(registry_db, setup["us"].id, ["INFERENCE_PROFILE"])
    body = {"litellm_model": f"bedrock/{NEW}"}

    response = _put(client, admin_token, "local-us1", f"bedrock/{NEW}", body)
    assert response.status_code == 422
    assert response.json()["detail"] == [NEEDS_PROFILE]

    ok(_put(client, admin_token, "local-us1", f"bedrock/{NEW}", body, "?force=true"), 201)


def test_put_that_turns_a_deployment_on_runs_the_enable_check(client, admin_token, registry_db, setup):
    # gpt-4o is disabled on the EU proxy, whose LiteLLM version does not support it.
    response = _put(client, admin_token, "local-eu1", "openai/gpt-4o", {"enabled": True})
    assert response.status_code == 422
    assert response.json()["detail"] == [UNSUPPORTED]

    assert ok(_put(client, admin_token, "local-eu1", "openai/gpt-4o", {"enabled": True}, "?force=true"))[0]["enabled"]


def test_put_that_changes_only_groups_or_credential_skips_the_enable_check(client, admin_token, setup):
    # claude-x is enabled on the US proxy, but its support is unknown.
    body = ok(_put(
        client, admin_token, "local-us1", "bedrock/anthropic.claude-x-v1:0", {"groups": ["alpha"], "credential": "c1"}
    ))

    assert body[0]["access_groups"] == ["alpha"]


@pytest.mark.parametrize(
    "call_types, problem", [(["PROVISIONED"], PROVISIONED), (["INFERENCE_PROFILE"], NEEDS_PROFILE)]
)
def test_put_that_changes_the_backend_of_an_enabled_deployment_runs_the_enable_check(
    client, admin_token, registry_db, setup, call_types, problem
):
    row = _new_bedrock_model(registry_db, setup["us"].id, call_types)
    registry_db.add(DBRegistryModelRegion(
        model_id=row.id, region_id=setup["us"].id, model_name=NEW, litellm_model=f"bedrock/us.{NEW}", enabled=True,
    ))
    registry_db.commit()
    body = {"litellm_model": f"bedrock/{NEW}"}

    response = _put(client, admin_token, "local-us1", f"bedrock/{NEW}", body)
    assert response.status_code == 422
    assert response.json()["detail"] == [problem]

    assert ok(_put(client, admin_token, "local-us1", f"bedrock/{NEW}", body, "?force=true"))[0]["litellm_model"] == (
        f"bedrock/{NEW}"
    )


@pytest.mark.parametrize(
    "eol_date, query, status",
    [(FUTURE, "", 409), (FUTURE, "?force=true", 200), (PAST, "", 200), (None, "", 200)],
)
def test_put_that_disables_checks_the_eol_date(client, admin_token, registry_db, setup, eol_date, query, status):
    model(registry_db, "openai", "gpt-4o").eol_date = eol_date
    registry_db.commit()

    response = _put(client, admin_token, "local-us1", "openai/gpt-4o", {"enabled": False}, query)

    assert response.status_code == status, response.text
    if status == 409:
        assert str(FUTURE) in response.json()["detail"]


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_put_refuses_a_rename_that_leaves_an_alias_with_no_target(client, admin_token, registry_db, setup, query):
    before = _state(registry_db)

    response = _put(client, admin_token, "local-us1", "openai/gpt-4o", {"model_name": "gpt-4o-new"}, query)

    assert response.status_code == 409
    assert "zeta" in response.json()["detail"]
    assert _state(registry_db) == before


def test_put_renames_when_another_deployment_keeps_the_alias_target(client, admin_token, registry_db, setup):
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add(DBRegistryModelRegion(
        model_id=old.id, region_id=setup["us"].id, model_name="gpt-4o", litellm_model="openai/gpt-old",
    ))
    registry_db.commit()

    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"model_name": "gpt-4o-new"}))

    assert [d["model_name"] for d in body] == ["gpt-4o-new"]


def test_put_refuses_a_model_name_that_is_an_alias(client, admin_token, setup):
    for key in ("openai/gpt-old", "openai/gpt-4o"):
        response = _put(client, admin_token, "local-us1", key, {"model_name": "chat"})
        assert response.status_code == 422
        assert response.json()["detail"] == "the model name chat is an alias on this proxy"

    # The alias lives only on the US proxy.
    ok(_put(client, admin_token, "local-eu1", "openai/gpt-old", {"model_name": "chat"}), 201)


def test_put_dry_run_returns_the_write_and_saves_nothing(client, admin_token, registry_db, setup):
    _link(registry_db, setup["us"].id, "gpt-4o", "beta")
    before = _state(registry_db)
    create = {"credential": "c1", "groups": ["alpha"]}
    change = {"credential": "c2", "groups": ["alpha"]}

    dry_create = ok(_put(client, admin_token, "local-us1", "openai/gpt-old", create, "?dry_run=true"), 201)
    dry_change = ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", change, "?dry_run=true"))
    assert _state(registry_db) == before

    assert ok(_put(client, admin_token, "local-us1", "openai/gpt-old", create), 201) == dry_create
    assert ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", change)) == dry_change


def test_put_reaches_a_model_id_with_a_slash(client, admin_token, registry_db, setup):
    row = add_model(registry_db, "deepinfra", "BAAI/bge-m3")
    registry_db.add(_support(row.id, setup["us"].id))
    registry_db.commit()

    body = ok(_put(client, admin_token, "local-us1", "deepinfra/BAAI/bge-m3"), 201)

    assert [(d["model_name"], d["litellm_model"]) for d in body] == [("BAAI/bge-m3", "deepinfra/BAAI/bge-m3")]


def _delete(client, token, proxy, key, query=""):
    return client.delete(f"{PROXIES}/{proxy}/models/{key}{query}", headers=auth(token))


def _deployments_of(db, provider, model_id):
    db.expire_all()
    row = model(db, provider, model_id)
    return sorted(
        (d.region_id, d.model_name) for d in db.query(DBRegistryModelRegion).filter_by(model_id=row.id)
    )


def test_delete_removes_every_deployment_on_the_proxy(client, admin_token, registry_db, setup):
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    registry_db.add(DBRegistryModelRegion(
        model_id=claude.id, region_id=setup["us"].id, model_name="claude-x-b", litellm_model=f"bedrock/{claude.model_id}",
    ))
    registry_db.add(DBRegistryModelRegion(
        model_id=claude.id, region_id=setup["eu"].id, model_name="claude-x", litellm_model=f"bedrock/{claude.model_id}",
    ))
    registry_db.commit()
    _link(registry_db, setup["us"].id, "claude-x-b", "alpha")
    # The alias `chat` points to claude-x on the US proxy, so move it first.
    registry_db.query(DBRegistryAlias).filter_by(alias="chat").delete()
    registry_db.commit()

    assert _delete(client, admin_token, "local-us1", "bedrock/anthropic.claude-x-v1:0").status_code == 204

    assert _deployments_of(registry_db, "bedrock", "anthropic.claude-x-v1:0") == [(setup["eu"].id, "claude-x")]
    assert registry_db.query(DBRegistryModelRegionGroup).count() == 0


@pytest.mark.parametrize(
    "proxy, key", [("local-us1", "openai/gpt-old"), ("nope", "openai/gpt-4o"), ("local-us1", "openai/nope")]
)
def test_delete_not_found(client, admin_token, setup, proxy, key):
    assert _delete(client, admin_token, proxy, key).status_code == 404


@pytest.mark.parametrize(
    "eol_date, query, status",
    [(FUTURE, "", 409), (FUTURE, "?force=true", 204), (PAST, "", 204), (None, "", 204)],
)
def test_delete_checks_the_eol_date(client, admin_token, registry_db, setup, eol_date, query, status):
    # The EU proxy has no alias, so only the EOL date can refuse.
    model(registry_db, "openai", "gpt-4o").eol_date = eol_date
    registry_db.commit()

    response = _delete(client, admin_token, "local-eu1", "openai/gpt-4o", query)

    assert response.status_code == status, response.text
    if status == 409:
        assert str(FUTURE) in response.json()["detail"]


@pytest.mark.parametrize("query", ["", "?force=true"])
def test_delete_refuses_to_leave_an_alias_with_no_target(client, admin_token, registry_db, setup, query):
    before = _state(registry_db)

    response = _delete(client, admin_token, "local-us1", "openai/gpt-4o", query)

    assert response.status_code == 409
    assert "zeta" in response.json()["detail"]
    assert _state(registry_db) == before


def test_delete_passes_when_another_deployment_keeps_the_alias_target(client, admin_token, registry_db, setup):
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add(DBRegistryModelRegion(
        model_id=old.id, region_id=setup["us"].id, model_name="gpt-4o", litellm_model="openai/gpt-old",
    ))
    registry_db.commit()

    assert _delete(client, admin_token, "local-us1", "openai/gpt-4o").status_code == 204
    assert _deployments_of(registry_db, "openai", "gpt-4o") == [(setup["eu"].id, "gpt-4o")]


def test_delete_dry_run_keeps_the_rows(client, admin_token, registry_db, setup):
    _link(registry_db, setup["eu"].id, "gpt-4o", "alpha")
    before = _state(registry_db)

    assert _delete(client, admin_token, "local-eu1", "openai/gpt-4o", "?dry_run=true").status_code == 204
    assert _state(registry_db) == before


def test_writes_on_a_proxy_that_was_never_imported_get_409(client, admin_token, registry_db, setup):
    registry_db.get(DBRegistryProxy, setup["eu"].id).imported_at = None
    registry_db.commit()
    before = _state(registry_db)

    for response in (
        _put(client, admin_token, "local-eu1", "openai/gpt-old"),
        _put(client, admin_token, "local-eu1", "openai/gpt-4o", {"groups": ["alpha"]}),
        _delete(client, admin_token, "local-eu1", "openai/gpt-4o"),
    ):
        assert response.status_code == 409
        assert response.json()["detail"] == "Import the proxy first: local-eu1"
    assert _state(registry_db) == before
