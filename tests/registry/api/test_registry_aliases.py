import json

import pytest

from app.db.models import DBRegion
from app.registry.models import DBRegistryAlias
from tests.registry.api.conftest import auth

ALIASES = "/registry/aliases"


@pytest.fixture
def aliases(registry_db, deployed):
    registry_db.add_all([
        DBRegistryAlias(region_id=deployed["us"].id, alias="zeta", target="gpt-4o"),
        DBRegistryAlias(region_id=deployed["us"].id, alias="chat", target="claude-x", hidden=True),
        DBRegistryAlias(region_id=deployed["eu"].id, alias="embed", target="gpt-4o"),
    ])
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
    response = _put(client, admin_token, "local-us1/chat", {"target": "claude-x"})
    assert response.status_code == 201, response.text
    assert response.json() == {"proxy": "local-us1", "alias": "chat", "target": "claude-x", "hidden": False}

    response = _put(client, admin_token, "local-us1/chat", {"target": "gpt-4o", "hidden": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"proxy": "local-us1", "alias": "chat", "target": "gpt-4o", "hidden": True}
    assert [(alias, target, hidden) for _, _, alias, target, hidden, _ in _state(registry_db)] == [
        ("chat", "gpt-4o", True),
    ]


def test_get_sorts_by_proxy_then_alias(client, admin_token, aliases):
    response = client.get(ALIASES, headers=auth(admin_token))

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "3"
    assert response.json() == [
        {"proxy": "local-eu1", "alias": "embed", "target": "gpt-4o", "hidden": False},
        {"proxy": "local-us1", "alias": "chat", "target": "claude-x", "hidden": True},
        {"proxy": "local-us1", "alias": "zeta", "target": "gpt-4o", "hidden": False},
    ]


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


def test_put_takes_a_name_of_128_allowed_characters(client, admin_token, deployed):
    name = "Az09._:-" * 16

    assert _put(client, admin_token, f"local-us1/{name}", {"target": "gpt-4o"}).status_code == 201


@pytest.mark.parametrize("proxy", ["nope", "local-ap1"])
def test_unknown_proxy_is_404(client, admin_token, registry_db, aliases, proxy):
    # A region with no registry_proxies row is not a proxy of the registry.
    registry_db.add(DBRegion(name="local-ap1", litellm_api_url="http://ap", litellm_api_key="sk", is_active=True))
    registry_db.commit()

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

    dry = call(client, admin_token, "?dry_run=true")
    assert _state(registry_db) == before

    real = call(client, admin_token, "")
    assert (dry.status_code, dry.content) == (real.status_code, real.content)
    assert _state(registry_db) != before
