import json

import pytest

from app.registry.models import DBRegistryAlias, DBRegistryProxy
from app.registry.planner import DELETES_EVERY_ALIAS, REMOVES_LIVE_ALIAS
from tests.registry.api.conftest import CHECKED_AT, EU_URL, US_URL, auth, ok

ALIASES = "/registry/aliases"


@pytest.fixture
def aliases(registry_db, deployed):
    registry_db.add_all([
        DBRegistryAlias(region_id=deployed["us"].id, alias="zeta", target="gpt-4o"),
        DBRegistryAlias(region_id=deployed["us"].id, alias="chat", target="claude-x", hidden=True),
        DBRegistryAlias(region_id=deployed["eu"].id, alias="embed", target="gpt-4o"),
    ])
    # The adopt import read the live aliases, so the plans show only the alias problems.
    for proxy in registry_db.query(DBRegistryProxy):
        proxy.adopted_at = CHECKED_AT
    registry_db.commit()


def _put(client, token, path, body, query=""):
    return client.put(f"{ALIASES}/{path}{query}", json=body, headers=auth(token))


def _delete(client, token, path, query=""):
    return client.delete(f"{ALIASES}/{path}{query}", headers=auth(token))


def _state(db):
    db.expire_all()
    return [
        (a.id, a.region_id, a.alias, a.target, a.hidden, a.updated_at)
        for a in db.query(DBRegistryAlias).order_by(DBRegistryAlias.id)
    ]


def test_put_creates_then_changes_an_alias(client, admin_token, registry_db, deployed):
    body = ok(_put(client, admin_token, "local-us1/chat", {"target": "claude-x"}), 201)
    assert body == {"proxy": "local-us1", "alias": "chat", "target": "claude-x", "hidden": False}

    body = ok(_put(client, admin_token, "local-us1/chat", {"target": "gpt-4o", "hidden": True}))
    assert body == {"proxy": "local-us1", "alias": "chat", "target": "gpt-4o", "hidden": True}
    assert [(alias, target, hidden) for _, _, alias, target, hidden, _ in _state(registry_db)] == [
        ("chat", "gpt-4o", True),
    ]


def test_get_sorts_by_proxy_then_alias(client, admin_token, aliases):
    response = client.get(ALIASES, headers=auth(admin_token))

    assert ok(response) == [
        {"proxy": "local-eu1", "alias": "embed", "target": "gpt-4o", "hidden": False},
        {"proxy": "local-us1", "alias": "chat", "target": "claude-x", "hidden": True},
        {"proxy": "local-us1", "alias": "zeta", "target": "gpt-4o", "hidden": False},
    ]
    assert response.headers["X-Total-Count"] == "3"


def test_get_filters_by_proxy(client, admin_token, aliases):
    response = client.get(f"{ALIASES}?proxy=local-us1", headers=auth(admin_token))
    assert response.headers["X-Total-Count"] == "2"
    assert [a["alias"] for a in response.json()] == ["chat", "zeta"]

    assert client.get(f"{ALIASES}?proxy=nope", headers=auth(admin_token)).json() == []


@pytest.mark.parametrize(
    "path, body",
    [
        # claude-x runs only on local-us1.
        ("local-eu1/chat", {"target": "claude-x"}),
        ("local-us1/gpt-4o", {"target": "claude-x"}),
        ("local-us1/a/b", {"target": "claude-x"}),
        ("local-us1/a b", {"target": "claude-x"}),
        ("local-us1/" + "a" * 129, {"target": "claude-x"}),
        ("local-us1/zeta", {"target": "claude-x", "hidden": None}),
        ("local-us1/zeta", {"target": ""}),
        ("local-us1/zeta", {"hidden": True}),
        ("local-us1/zeta", {"target": "claude-x", "default": True}),
        # json.dumps writes the JSON literal Infinity, which Python's parser reads back.
        ("local-us1/zeta", {"target": float("inf")}),
    ],
)
def test_put_rejects_a_bad_alias_or_body(client, admin_token, registry_db, aliases, path, body):
    before = _state(registry_db)

    response = client.put(
        f"{ALIASES}/{path}", content=json.dumps(body),
        headers={**auth(admin_token), "Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert _state(registry_db) == before


def test_put_lists_both_name_problems(client, admin_token, registry_db, aliases):
    before = _state(registry_db)

    response = _put(client, admin_token, "local-us1/gpt-4o", {"target": "nope"})

    assert response.status_code == 422, response.text
    assert response.json()["problems"] == [
        {"message": "The alias is the name of a deployment on this proxy", "proxy": "local-us1", "model": None},
        {"message": "The target is not a deployment on this proxy", "proxy": "local-us1", "model": None},
    ]
    assert _state(registry_db) == before


def test_put_takes_a_name_of_128_allowed_characters(client, admin_token, deployed):
    name = "Az09._:-" * 16

    assert _put(client, admin_token, f"local-us1/{name}", {"target": "gpt-4o"}).status_code == 201


@pytest.mark.parametrize("proxy", ["nope", "local-ap1"])
def test_unknown_proxy_is_404(client, admin_token, aliases, bare_region, proxy):
    assert _put(client, admin_token, f"{proxy}/chat", {"target": "gpt-4o"}).status_code == 404
    assert _delete(client, admin_token, f"{proxy}/chat").status_code == 404


def test_delete_removes_the_alias(client, admin_token, registry_db, aliases):
    assert _delete(client, admin_token, "local-us1/zeta").status_code == 204
    assert [alias for _, _, alias, _, _, _ in _state(registry_db)] == ["chat", "embed"]


def test_delete_unknown_alias_is_404(client, admin_token, aliases):
    # embed is an alias on local-eu1 only.
    assert _delete(client, admin_token, "local-us1/embed").status_code == 404
    assert _delete(client, admin_token, "local-us1/a/b").status_code == 404


@pytest.mark.parametrize(
    "call",
    [
        lambda client, token, query: _put(client, token, "local-us1/new", {"target": "gpt-4o"}, query),
        lambda client, token, query: _put(client, token, "local-us1/zeta", {"target": "claude-x", "hidden": True}, query),
        lambda client, token, query: _delete(client, token, "local-us1/zeta", query),
    ],
    ids=["create", "change", "delete"],
)
def test_dry_run_answers_like_the_write_and_saves_nothing(client, admin_token, registry_db, aliases, call):
    before = _state(registry_db)

    dry = ok(call(client, admin_token, "?dry_run=true"))
    assert _state(registry_db) == before

    real = call(client, admin_token, "")
    assert dry["result"] == (None if real.status_code == 204 else real.json())
    assert _state(registry_db) != before


def test_put_dry_run_plans_the_full_alias_map(client, admin_token, aliases, live_proxies):
    live_proxies.by_url[US_URL] = {"aliases": {"zeta": "gpt-4o", "old": "gpt-4o"}}

    body = ok(_put(client, admin_token, "local-us1/new", {"target": "gpt-4o"}, "?dry_run=true"))

    assert body["steps"] == [{
        "proxy": "local-us1", "action": "write_alias_map", "model": None, "model_name": None, "deployment_id": None,
        "credential": None, "blocked": False,
        "payload": {"zeta": "gpt-4o", "chat": {"model": "claude-x", "hidden": True}, "new": "gpt-4o"},
        "changes": {
            "chat": {"from": None, "to": {"model": "claude-x", "hidden": True}},
            "new": {"from": None, "to": "gpt-4o"},
            "old": {"from": "gpt-4o", "to": None},
        },
        "problems": [REMOVES_LIVE_ALIAS.format("old")],
    }]


def test_delete_dry_run_does_not_warn_for_the_deleted_alias(client, admin_token, aliases, live_proxies):
    live_proxies.by_url[US_URL] = {"aliases": {"zeta": "gpt-4o", "chat": {"model": "claude-x", "hidden": True}}}

    body = ok(_delete(client, admin_token, "local-us1/zeta", "?dry_run=true"))

    [step] = body["steps"]
    assert step["changes"] == {"zeta": {"from": "gpt-4o", "to": None}}
    assert step["problems"] == []


def test_delete_dry_run_of_the_last_alias_warns(client, admin_token, aliases, live_proxies):
    live_proxies.by_url[EU_URL] = {"aliases": {"embed": "gpt-4o", "other": "gpt-4o"}}

    body = ok(_delete(client, admin_token, "local-eu1/embed", "?dry_run=true"))

    [step] = body["steps"]
    assert (step["action"], step["payload"]) == ("write_alias_map", {})
    # The deleted alias gets no problem of its own; the other live alias does.
    assert step["problems"] == [DELETES_EVERY_ALIAS, REMOVES_LIVE_ALIAS.format("other")]


def test_alias_dry_run_on_a_proxy_that_was_never_adopted_is_blocked(client, admin_token, deployed, live_proxies):
    body = ok(_put(client, admin_token, "local-us1/new", {"target": "gpt-4o"}, "?dry_run=true"))

    [step] = body["steps"]
    assert step["problems"][0].startswith("the adopt import has not read the live aliases")
    assert "a write to a registry-managed proxy that it can read imports the proxy first" in step["problems"][0]
    assert step["blocked"] is True
