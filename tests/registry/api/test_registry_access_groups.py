import pytest
from sqlalchemy import event

from app.db.models import DBModelAccessGroup, DBRegion
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
)
from tests.registry.api.conftest import TODAY, auth, model

GROUPS = "/registry/access-groups"


@pytest.fixture
def groups(registry_db, deployed):
    """`beta` on three models, one of them under two names on local-us1; `alpha` on none."""
    gpt = model(registry_db, "openai", "gpt-4o")
    second = DBRegistryModelRegion(
        model_id=gpt.id, region_id=deployed["us"].id, model_name="chat-gpt",
        litellm_model="openai/gpt-4o", litellm_deployment_id="us-gpt-2", enabled=True,
    )
    beta = DBRegistryAccessGroup(slug="beta", label="Beta", description="B")
    alpha = DBRegistryAccessGroup(slug="alpha", label="Alpha")
    registry_db.add_all([second, beta, alpha])
    registry_db.flush()
    registry_db.add_all(
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=beta.id)
        for deployment in registry_db.query(DBRegistryModelRegion)
    )
    registry_db.commit()


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _groups_state(db):
    db.expire_all()
    return [
        (g.id, g.slug, g.label, g.description)
        for g in db.query(DBRegistryAccessGroup).order_by(DBRegistryAccessGroup.id)
    ]


BETA_MODELS = [
    {"proxy": "local-eu1", "model": "openai/gpt-4o", "model_names": ["gpt-4o"]},
    {"proxy": "local-us1", "model": "bedrock/anthropic.claude-x-v1:0", "model_names": ["claude-x"]},
    {"proxy": "local-us1", "model": "openai/gpt-4o", "model_names": ["chat-gpt", "gpt-4o"]},
]


def test_list_sorts_groups_and_shows_their_models(client, admin_token, groups):
    response = client.get(GROUPS, headers=auth(admin_token))

    alpha, beta = _ok(response)
    assert response.headers["X-Total-Count"] == "2"
    assert alpha == {"slug": "alpha", "label": "Alpha", "description": None, "models": []}
    assert beta == {"slug": "beta", "label": "Beta", "description": "B", "models": BETA_MODELS}


def test_list_runs_the_same_queries_for_more_groups(client, admin_token, registry_db, deployed):
    deployment = registry_db.query(DBRegistryModelRegion).first()

    def add_group(slug):
        group = DBRegistryAccessGroup(slug=slug, label=slug)
        registry_db.add(group)
        registry_db.flush()
        registry_db.add(DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=group.id))
        registry_db.commit()

    statements = []

    def count(*args):
        statements.append(args[2])

    add_group("one")
    # The session's own engine: tests/conftest.py loads as a second module, with another engine.
    engine = registry_db.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert len(_ok(client.get(GROUPS, headers=auth(admin_token)))) == 1
        one = len(statements)
        assert one
        add_group("two")
        add_group("three")
        statements.clear()
        assert len(_ok(client.get(GROUPS, headers=auth(admin_token)))) == 3
        assert len(statements) == one
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_post_creates_a_group(client, admin_token, registry_db, groups):
    body = _ok(client.post(GROUPS, json={"slug": "gamma-2", "label": "Gamma"}, headers=auth(admin_token)), 201)

    assert body == {"slug": "gamma-2", "label": "Gamma", "description": None, "models": []}
    assert ("gamma-2", "Gamma") in [(slug, label) for _, slug, label, _ in _groups_state(registry_db)]


def test_post_with_a_known_slug_is_409(client, admin_token, registry_db, groups):
    before = _groups_state(registry_db)

    response = client.post(GROUPS, json={"slug": "beta", "label": "Other"}, headers=auth(admin_token))

    assert response.status_code == 409
    assert _groups_state(registry_db) == before


@pytest.mark.parametrize(
    "body",
    [
        {"slug": "Abc", "label": "x"},
        {"slug": "-abc", "label": "x"},
        {"slug": "abc-", "label": "x"},
        {"slug": "a" * 65, "label": "x"},
        {"slug": "", "label": "x"},
        {"slug": "abc"},
        {"slug": "abc", "label": ""},
        {"slug": "abc", "label": "x", "public": True},
    ],
)
def test_post_rejects_a_bad_body(client, admin_token, registry_db, groups, body):
    before = _groups_state(registry_db)

    assert client.post(GROUPS, json=body, headers=auth(admin_token)).status_code == 422
    assert _groups_state(registry_db) == before


def test_patch_changes_only_the_fields_in_the_body(client, admin_token, groups):
    body = _ok(client.patch(f"{GROUPS}/beta", json={"label": "Beta 2"}, headers=auth(admin_token)))
    assert (body["label"], body["description"], body["models"]) == ("Beta 2", "B", BETA_MODELS)

    body = _ok(client.patch(f"{GROUPS}/beta", json={"description": None}, headers=auth(admin_token)))
    assert (body["slug"], body["label"], body["description"]) == ("beta", "Beta 2", None)


@pytest.mark.parametrize("body", [{}, {"slug": "x"}, {"label": None}, {"label": ""}])
def test_patch_rejects_a_bad_body(client, admin_token, registry_db, groups, body):
    before = _groups_state(registry_db)

    assert client.patch(f"{GROUPS}/beta", json=body, headers=auth(admin_token)).status_code == 422
    assert _groups_state(registry_db) == before


def test_patch_unknown_slug_is_404(client, admin_token, groups):
    assert client.patch(f"{GROUPS}/nope", json={"label": "x"}, headers=auth(admin_token)).status_code == 404


def test_dry_run_of_post_and_patch_saves_nothing(client, admin_token, registry_db, groups):
    before = _groups_state(registry_db)

    body = _ok(client.post(
        f"{GROUPS}?dry_run=true", json={"slug": "gamma", "label": "Gamma"}, headers=auth(admin_token)
    ), 201)
    assert body["slug"] == "gamma"
    body = _ok(client.patch(
        f"{GROUPS}/beta?dry_run=true", json={"label": "New", "description": None}, headers=auth(admin_token)
    ))
    assert (body["label"], body["description"], body["models"]) == ("New", None, BETA_MODELS)

    assert _groups_state(registry_db) == before


def test_group_writes_need_a_system_admin(client, test_token, groups):
    calls = [
        lambda headers: client.post(GROUPS, json={"slug": "gamma", "label": "G"}, headers=headers),
        lambda headers: client.patch(f"{GROUPS}/beta", json={"label": "G"}, headers=headers),
    ]
    for call in calls:
        assert call({}).status_code == 401
        assert call(auth(test_token)).status_code == 403


US_GPT = {"proxy": "local-us1", "model": "openai/gpt-4o"}


def _put(client, token, slug, models, query=""):
    return client.put(f"{GROUPS}/{slug}/models{query}", json={"models": models}, headers=auth(token))


def _links_state(db):
    db.expire_all()
    return sorted(
        (link.access_group_id, link.model_region_id) for link in db.query(DBRegistryModelRegionGroup)
    )


def test_put_links_every_deployment_of_the_model_on_that_proxy(client, admin_token, registry_db, groups):
    body = _ok(_put(client, admin_token, "alpha", [US_GPT]))

    assert body["models"] == [{**US_GPT, "model_names": ["chat-gpt", "gpt-4o"]}]
    alpha = registry_db.query(DBRegistryAccessGroup).filter_by(slug="alpha").one()
    assert registry_db.query(DBRegistryModelRegionGroup).filter_by(access_group_id=alpha.id).count() == 2


def test_put_removes_the_group_from_every_model_left_out(client, admin_token, registry_db, groups):
    body = _ok(_put(client, admin_token, "beta", [US_GPT, US_GPT]))

    assert body["models"] == [{**US_GPT, "model_names": ["chat-gpt", "gpt-4o"]}]
    assert len(_links_state(registry_db)) == 2


def test_put_with_an_empty_list_clears_the_group(client, admin_token, registry_db, groups):
    assert _ok(_put(client, admin_token, "beta", []))["models"] == []
    assert _links_state(registry_db) == []


@pytest.mark.parametrize(
    "entries",
    [
        [US_GPT, {"proxy": "nope", "model": "openai/gpt-4o"}],
        [US_GPT, {"proxy": "local-ap1", "model": "openai/gpt-4o"}],
        [US_GPT, {"proxy": "local-us1", "model": "openai/nope"}],
        [US_GPT, {"proxy": "local-us1", "model": "gpt-4o"}],
        [US_GPT, {"proxy": "local-eu1", "model": "bedrock/anthropic.claude-x-v1:0"}],
        [US_GPT, {"proxy": "local-us1", "model": "openai/gpt-old"}],
        [{**US_GPT, "enabled": True}],
        [{"proxy": "local-us1"}],
    ],
)
def test_put_rejects_a_bad_entry_and_changes_nothing(client, admin_token, registry_db, groups, entries):
    # A region with no registry_proxies row is not a proxy of the registry.
    registry_db.add(DBRegion(name="local-ap1", litellm_api_url="http://ap", litellm_api_key="sk", is_active=True))
    registry_db.commit()
    before = _links_state(registry_db)

    assert _put(client, admin_token, "alpha", entries).status_code == 422
    assert _put(client, admin_token, "beta", entries).status_code == 422
    assert _links_state(registry_db) == before


def test_put_without_the_models_field_is_422(client, admin_token, groups):
    response = client.put(f"{GROUPS}/beta/models", json={}, headers=auth(admin_token))

    assert response.status_code == 422


def test_put_unknown_slug_is_404(client, admin_token, groups):
    assert _put(client, admin_token, "nope", [US_GPT]).status_code == 404


def test_put_reaches_a_model_id_with_a_slash(client, admin_token, registry_db, groups, deployed):
    deepinfra = DBRegistryProvider(name="deepinfra")
    registry_db.add(deepinfra)
    registry_db.flush()
    bge = DBRegistryModel(
        provider_id=deepinfra.id, model_id="BAAI/bge-m3", source="litellm", first_seen=TODAY, last_seen=TODAY,
    )
    registry_db.add(bge)
    registry_db.flush()
    registry_db.add(DBRegistryModelRegion(
        model_id=bge.id, region_id=deployed["us"].id, model_name="bge-m3",
        litellm_model="deepinfra/BAAI/bge-m3", litellm_deployment_id="us-bge",
    ))
    registry_db.commit()

    body = _ok(_put(client, admin_token, "alpha", [{"proxy": "local-us1", "model": "deepinfra/BAAI/bge-m3"}]))

    assert body["models"] == [{"proxy": "local-us1", "model": "deepinfra/BAAI/bge-m3", "model_names": ["bge-m3"]}]


def test_put_dry_run_saves_nothing(client, admin_token, registry_db, groups):
    before = _links_state(registry_db)

    body = _ok(_put(client, admin_token, "beta", [US_GPT], "?dry_run=true"))

    assert body["models"] == [{**US_GPT, "model_names": ["chat-gpt", "gpt-4o"]}]
    assert _links_state(registry_db) == before


def test_put_needs_a_system_admin(client, test_token, groups):
    assert client.put(f"{GROUPS}/beta/models", json={"models": []}).status_code == 401
    assert _put(client, test_token, "beta", []).status_code == 403


def _delete(client, token, slug, query=""):
    return client.delete(f"{GROUPS}/{slug}{query}", headers=auth(token))


def _slugs(db):
    return [slug for _, slug, _, _ in _groups_state(db)]


def test_delete_removes_the_group(client, admin_token, registry_db, groups):
    assert _delete(client, admin_token, "alpha").status_code == 204
    assert _slugs(registry_db) == ["beta"]


def test_delete_is_409_while_the_old_catalog_has_the_slug(client, admin_token, registry_db, groups):
    registry_db.add(DBModelAccessGroup(slug="alpha", label="Alpha"))
    registry_db.commit()

    response = _delete(client, admin_token, "alpha")

    assert response.status_code == 409, response.text
    assert "alpha" in _slugs(registry_db)


def test_delete_is_409_while_a_deployment_carries_the_group(client, admin_token, registry_db, groups):
    assert _delete(client, admin_token, "beta").status_code == 409
    assert "beta" in _slugs(registry_db)

    _ok(_put(client, admin_token, "beta", []))

    assert _delete(client, admin_token, "beta").status_code == 204
    assert _slugs(registry_db) == ["alpha"]


def test_delete_unknown_slug_is_404(client, admin_token, groups):
    assert _delete(client, admin_token, "nope").status_code == 404


def test_delete_dry_run_keeps_the_group(client, admin_token, registry_db, groups):
    before = _groups_state(registry_db)

    assert _delete(client, admin_token, "alpha", "?dry_run=true").status_code == 204
    assert _groups_state(registry_db) == before


def test_delete_needs_a_system_admin(client, test_token, groups):
    assert client.delete(f"{GROUPS}/alpha").status_code == 401
    assert _delete(client, test_token, "alpha").status_code == 403
