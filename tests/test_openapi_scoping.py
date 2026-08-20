"""The published OpenAPI schema shows only what the caller is entitled to see.

Anonymous callers get the endpoints that need no authentication, authenticated
users additionally get the authenticated ones, and system admins get everything.
This is visibility only — every endpoint still enforces its own authorization.
"""

from app.main import _OPENAPI_TIERS, _TIER_RANK, _scope_schema, app

ADMIN_ONLY_PATH = "/regions/admin"
USER_PATH = "/auth/me"
PUBLIC_PATH = "/health"


def _operations(schema):
    return {
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method in item
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }


def test_every_operation_in_the_schema_has_a_tier():
    """An unmapped operation is hidden from everyone but admins.

    This is the guard for a FastAPI upgrade that changes how included routers
    expose their operations: the map would silently go empty and the public
    docs would lose almost every endpoint.
    """
    full = app.openapi()
    unmapped = [
        (method, path)
        for path, item in full["paths"].items()
        for method, operation in item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
        and operation.get("operationId") not in _OPENAPI_TIERS
    ]
    assert unmapped == []


def test_tier_map_covers_routes_from_included_routers():
    """Routes added with include_router are not flattened into app.routes."""
    full = app.openapi()
    converter_route = full["paths"]["/admin/models/{model_id_or_id}"]["get"]
    assert _OPENAPI_TIERS[converter_route["operationId"]] == "admin"
    team_route = full["paths"]["/auth/me"]["get"]
    assert _OPENAPI_TIERS[team_route["operationId"]] == "user"


def test_anonymous_schema_hides_authenticated_and_admin_paths():
    public = _scope_schema(app.openapi(), 0)
    assert PUBLIC_PATH in public["paths"]
    assert USER_PATH not in public["paths"]
    assert ADMIN_ONLY_PATH not in public["paths"]


def test_user_schema_hides_admin_paths_but_shows_public_ones():
    user = _scope_schema(app.openapi(), 1)
    assert PUBLIC_PATH in user["paths"]
    assert USER_PATH in user["paths"]
    assert ADMIN_ONLY_PATH not in user["paths"]


def test_admin_schema_is_the_full_schema():
    full = app.openapi()
    admin = _scope_schema(full, 2)
    assert _operations(admin) == _operations(full)
    assert admin["components"]["schemas"].keys() == full["components"]["schemas"].keys()


def test_scoping_widens_monotonically():
    full = app.openapi()
    public, user, admin = (_scope_schema(full, rank) for rank in (0, 1, 2))
    assert _operations(public) < _operations(user) < _operations(admin)


def test_hidden_operations_do_not_leave_their_models_behind(client):
    """Component schemas are pruned too, or they still describe hidden endpoints."""
    public = _scope_schema(app.openapi(), 0)
    kept = public["components"]["schemas"]

    assert "RegionAdminResponse" not in kept
    # Models still used by a visible operation survive, including nested ones.
    assert len(kept) < len(app.openapi()["components"]["schemas"])


def test_tags_are_pruned_to_visible_operations():
    """Tag descriptions must not survive the operations they label.

    The document does not carry a top-level tags list today, so this exercises
    _scope_schema directly.
    """
    schema = {
        "paths": {
            "/health": {"get": {"operationId": "health_check_health_get"}},
            "/regions/admin": {"get": {"operationId": "hidden", "tags": ["regions"]}},
        },
        "components": {"schemas": {}},
        "tags": [{"name": "regions"}, {"name": "system"}],
    }

    scoped = _scope_schema(schema, _TIER_RANK["public"])

    assert "/regions/admin" not in scoped["paths"]
    assert scoped["tags"] == []


def test_scoping_does_not_mutate_the_cached_schema():
    full = app.openapi()
    before = len(full["paths"])
    _scope_schema(full, 0)
    assert len(app.openapi()["paths"]) == before


def test_anonymous_request_gets_the_public_schema(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert PUBLIC_PATH in paths
    assert ADMIN_ONLY_PATH not in paths


def test_user_jwt_gets_the_user_schema(client, test_token):
    response = client.get(
        "/openapi.json", headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert USER_PATH in paths
    assert ADMIN_ONLY_PATH not in paths


def test_admin_jwt_gets_the_full_schema(client, admin_token):
    response = client.get(
        "/openapi.json", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert ADMIN_ONLY_PATH in response.json()["paths"]


def test_api_token_is_recognised_not_treated_as_anonymous(client, test_token):
    """An API token must resolve to its owner, or users see the public schema."""
    created = client.post(
        "/auth/token",
        json={"name": "openapi-scope"},
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert created.status_code == 200, created.text
    api_token = created.json()["token"]

    response = client.get(
        "/openapi.json", headers={"Authorization": f"Bearer {api_token}"}
    )
    assert response.status_code == 200
    assert USER_PATH in response.json()["paths"]


def test_invalid_credentials_fall_back_to_the_public_schema(client):
    response = client.get(
        "/openapi.json", headers={"Authorization": "Bearer not-a-token"}
    )
    assert response.status_code == 200
    assert USER_PATH not in response.json()["paths"]
