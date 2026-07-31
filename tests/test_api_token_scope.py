"""Read-only vs read/write management API tokens.

A read-scoped token may only use safe HTTP methods. Enforcement lives in
app/core/security.py so it applies to every route without per-endpoint opt-in.
"""

import pytest

from app.db.models import DBAPIToken


def _make_token(db, user, scope, token="scoped-token"):
    db_token = DBAPIToken(
        name=f"{scope} token", token=token, user_id=user.id, scope=scope
    )
    db.add(db_token)
    db.commit()
    return token


def test_new_token_defaults_to_read(client, test_token):
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "Unspecified scope"},
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "read"


def test_write_scope_is_opt_in(client, test_token):
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "Writer", "scope": "write"},
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "write"


def test_unknown_scope_is_rejected(client, test_token):
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "Bad scope", "scope": "admin"},
    )

    assert response.status_code == 422


def test_read_token_can_read(client, db, test_user):
    token = _make_token(db, test_user, "read", token="read-token-get")

    response = client.get("/auth/token", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "method,path,payload",
    [
        # A token-management write, a resource write, and a delete — all must be
        # refused for a read-only token regardless of the user's own RBAC.
        ("post", "/auth/token", {"name": "nested"}),
        ("post", "/private-ai-keys/token", {"region_id": 1, "name": "k"}),
        ("delete", "/auth/token/1", None),
    ],
)
def test_read_token_cannot_write(client, db, test_user, method, path, payload):
    token = _make_token(db, test_user, "read", token=f"read-token-{method}-{len(path)}")

    request = getattr(client, method)
    response = (
        request(path, headers={"Authorization": f"Bearer {token}"}, json=payload)
        if payload is not None
        else request(path, headers={"Authorization": f"Bearer {token}"})
    )

    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]


def test_write_token_can_write(client, db, test_user):
    token = _make_token(db, test_user, "write", token="write-token-post")

    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Created by a write token"},
    )

    assert response.status_code == 200
    # The new token is read-only even though its creator could write.
    assert response.json()["scope"] == "read"


def test_jwt_session_is_not_scope_restricted(client, test_token):
    """Scope belongs to API tokens; a browser/JWT session is unaffected."""
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "From a JWT session"},
    )

    assert response.status_code == 200
