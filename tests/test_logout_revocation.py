"""Logout must stop the presented access token from working again (CWE-613)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from jose import jwt

from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import DBRevokedToken
from app.services.token_revocation import (
    prune_revoked_tokens,
    revoke_access_token,
)


def _claims(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        options={"verify_exp": False},
    )


def test_access_token_carries_a_unique_jti():
    first = _claims(create_access_token(data={"sub": "a@example.com"}))
    second = _claims(create_access_token(data={"sub": "a@example.com"}))

    assert first["jti"]
    assert first["jti"] != second["jti"]
    assert "iat" in first


def test_token_without_jti_is_rejected(client, test_user):
    # Shaped like a token signed before revocation existed.
    legacy = jwt.encode(
        {
            "sub": test_user.email,
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {legacy}"})

    assert response.status_code == 401


def test_logout_revokes_the_cookie_token(client, test_user):
    login = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    # The cookie is set directly: login marks it Secure, and the test client
    # talks plain http, so its own jar would drop it.
    client.cookies.set("access_token", token)
    assert client.get("/auth/me").status_code == 200

    assert client.post("/auth/logout").status_code == 200

    # Replay the same cookie, the way a stolen one would be used.
    client.cookies.set("access_token", token)
    assert client.get("/auth/me").status_code == 401


def test_logout_revokes_a_bearer_header_token(client, test_user, test_token):
    headers = {"Authorization": f"Bearer {test_token}"}
    assert client.get("/auth/me", headers=headers).status_code == 200

    assert client.post("/auth/logout", headers=headers).status_code == 200

    assert client.get("/auth/me", headers=headers).status_code == 401


def test_revoked_token_is_rejected_on_both_transports(client, test_user, test_token):
    # Both transports accept the token first, so the 401s below prove revocation
    # and not a transport the app simply ignores.
    client.cookies.set("access_token", test_token)
    assert client.get("/auth/me").status_code == 200
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {test_token}"}
        ).status_code
        == 200
    )

    client.cookies.clear()
    client.post("/auth/logout", headers={"Authorization": f"Bearer {test_token}"})

    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {test_token}"}
        ).status_code
        == 401
    )
    client.cookies.set("access_token", test_token)
    assert client.get("/auth/me").status_code == 401


def test_logout_does_not_affect_the_users_other_sessions(client, test_user):
    first = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    ).json()["access_token"]
    second = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"},
    ).json()["access_token"]

    client.post("/auth/logout", headers={"Authorization": f"Bearer {first}"})

    assert (
        client.get("/auth/me", headers={"Authorization": f"Bearer {first}"}).status_code
        == 401
    )
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {second}"}
        ).status_code
        == 200
    )


def test_revoked_token_cannot_be_refreshed_by_validate_jwt(
    client, test_user, test_token
):
    # The refresh endpoint decodes the token itself, so it needs its own check.
    # Without one, a revoked token buys a fresh one and undoes the logout.
    assert client.get(f"/auth/validate-jwt?token={test_token}").status_code == 200

    client.post("/auth/logout", headers={"Authorization": f"Bearer {test_token}"})

    query = client.get(f"/auth/validate-jwt?token={test_token}")
    header = client.get(
        "/auth/validate-jwt", headers={"Authorization": f"Bearer {test_token}"}
    )

    assert query.status_code == 401
    assert header.status_code == 401
    assert "access_token" not in query.json()


def test_validate_jwt_refuses_a_token_without_a_jti(client, test_user):
    legacy = jwt.encode(
        {"sub": test_user.email, "exp": datetime.now(UTC) + timedelta(minutes=30)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    assert client.get(f"/auth/validate-jwt?token={legacy}").status_code == 401


@patch("app.api.auth.SESService")
def test_expired_revoked_token_gets_no_recovery_email(mock_ses, client, db, test_user):
    mock_ses.return_value.send_email.return_value = True
    token = create_access_token(
        data={"sub": test_user.email}, expires_delta=timedelta(minutes=30)
    )
    revoke_access_token(db, token)

    # Same token, now past its expiry, replayed at the refresh endpoint.
    expired_revoked = jwt.encode(
        {
            "sub": test_user.email,
            "jti": _claims(token)["jti"],
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    response = client.get(f"/auth/validate-jwt?token={expired_revoked}")

    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"
    mock_ses.return_value.send_email.assert_not_called()


@patch("app.api.auth.SESService")
def test_expired_token_without_a_jti_still_gets_a_recovery_email(
    mock_ses, client, test_user
):
    mock_ses.return_value.send_email.return_value = True
    # An old emailed link: it predates revocation, so recovery must still work.
    legacy_expired = jwt.encode(
        {"sub": test_user.email, "exp": datetime.now(UTC) - timedelta(seconds=1)},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    response = client.get(f"/auth/validate-jwt?token={legacy_expired}")

    assert response.status_code == 401
    assert "validation URL has been sent" in response.json()["detail"]
    mock_ses.return_value.send_email.assert_called_once()


def test_logout_leaves_api_tokens_working(client, db, test_user, test_token):
    created = client.post(
        "/auth/token",
        json={"name": "cli"},
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert created.status_code == 200
    api_token = created.json()["token"]

    client.post("/auth/logout", headers={"Authorization": f"Bearer {api_token}"})

    # An API token is opaque and has its own delete endpoint, so logout must not
    # take it away.
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {api_token}"}
        ).status_code
        == 200
    )


def test_logout_without_a_token_still_succeeds(client):
    assert client.post("/auth/logout").status_code == 200


def test_logout_with_a_forged_token_succeeds_and_stores_nothing(client, db):
    forged = jwt.encode(
        {"sub": "nobody@example.com", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "not-the-real-secret",
        algorithm=settings.ALGORITHM,
    )

    response = client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {forged}"}
    )

    assert response.status_code == 200
    assert db.query(DBRevokedToken).count() == 0


def test_revoking_the_same_token_twice_is_harmless(db, test_user):
    token = create_access_token(data={"sub": test_user.email})

    assert revoke_access_token(db, token) is True
    assert revoke_access_token(db, token) is True
    assert db.query(DBRevokedToken).filter(DBRevokedToken.jti.isnot(None)).count() == 1


def test_expired_token_needs_no_denylist_row(db, test_user):
    expired = create_access_token(
        data={"sub": test_user.email}, expires_delta=timedelta(minutes=-5)
    )

    assert revoke_access_token(db, expired) is True
    assert db.query(DBRevokedToken).count() == 0


def test_revocation_records_the_token_owner(db, test_user):
    revoke_access_token(db, create_access_token(data={"sub": test_user.email}))

    row = db.query(DBRevokedToken).one()
    assert row.user_id == test_user.id
    assert row.expires_at > datetime.now(UTC)


@pytest.mark.parametrize("expired", [True, False])
def test_prune_removes_only_rows_past_retention(db, test_user, expired):
    age_days = settings.REVOKED_TOKENS_RETENTION_DAYS + 1 if expired else 0
    db.add(
        DBRevokedToken(
            jti="jti-under-test",
            user_id=test_user.id,
            expires_at=datetime.now(UTC) - timedelta(days=age_days),
        )
    )
    db.commit()

    deleted = prune_revoked_tokens(db)

    assert deleted == (1 if expired else 0)
    assert db.query(DBRevokedToken).count() == (0 if expired else 1)
