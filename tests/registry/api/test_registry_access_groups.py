import pytest
from sqlalchemy import event

from app.registry.models import DBRegistryAccessGroup, DBRegistryModelRegion, DBRegistryModelRegionGroup
from tests.registry.api.conftest import auth, model

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
