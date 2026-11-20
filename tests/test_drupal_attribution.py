"""
Tests for Drupal-origin moad delegation via X-Amazee-Source header.

POST /private-ai-keys delegates to moad by default (no header = Drupal).
When X-Amazee-Source: frontend is present, a caller with a team takes the
direct creation path. A teamless non-admin is delegated whatever the header
says.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.private_ai_keys import PROVISION_LOCK_KEY_SQL, _delegate_to_moad
from app.db.models import DBPrivateAIKey, DBRegion, DBSignupEvent, DBUser, DBTeam
from app.core.security import create_access_token, get_password_hash
from app.core.roles import UserRole
from app.schemas.models import PrivateAIKeyCreate
from app.services.signup_velocity import PROVISION_ENDPOINT
from tests.conftest import TestingSessionLocal, engine

EMAIL = "test-drupal@example.com"


def _make_user(
    db, email=EMAIL, role=UserRole.DEFAULT, is_admin=False, team_id=None, password=None
):
    # Email-code users have no password; only /auth/register sets one.
    user = DBUser(
        email=email,
        hashed_password=get_password_hash(password) if password else None,
        is_active=True,
        is_admin=is_admin,
        role=role,
        team_id=team_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, user):
    return create_access_token(data={"sub": user.email})


def _post_key(client, token, region_id, extra_headers=None):
    h = {"Authorization": f"Bearer {token}"}
    if extra_headers:
        h.update(extra_headers)
    return client.post(
        "/private-ai-keys",
        json={"region_id": region_id, "name": "test-key"},
        headers=h,
    )


# ---------------------------------------------------------------------------
# 1. No header → delegate to moad (Drupal path)
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_no_header_delegates_to_moad(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A request without X-Amazee-Source must be delegated to moad."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    token = _login(drupal_client, user)

    litellm_token = "drupal-key-001"
    # Use a distinct name from the POSTed key ("test-key") so the idempotency
    # check (which matches on name + region) does not short-circuit and the
    # full delegation path is exercised. This key is found afterwards via the
    # database_name lookup that follows the moad call.
    pre_created_key = DBPrivateAIKey(
        name="pre-seeded-key",
        litellm_token=litellm_token,
        litellm_api_url="http://test-llm",
        database_name="db",
        database_host="host",
        database_username="u",
        database_password="p",
        region_id=test_region.id,
    )
    db.add(pre_created_key)
    db.commit()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"llm": {"token": litellm_token}, "vectorDb": {"database": "db"}}

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = mock_http

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 200
    mock_http.post.assert_called_once()
    call_url = mock_http.post.call_args[0][0]
    assert "provision-key" in call_url


# ---------------------------------------------------------------------------
# 2. With header → direct creation, but only for a caller with a team
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_with_header_bypasses_moad(
    mock_client_cls, mock_settings, client, db, test_region, test_team
):
    """A non-admin team member with the X-Amazee-Source header must NOT be
    delegated. This is the legacy frontend flow.

    This specifically exercises the header bypass path, not the admin exemption.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    # Deliberately non-admin — bypass must come from the header, not the
    # is_admin exemption. A user with a team must carry a team role.
    user = _make_user(
        db, is_admin=False, role=UserRole.KEY_CREATOR, team_id=test_team.id
    )
    token = _login(client, user)

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = mock_http

    with (
        patch(
            "app.api.private_ai_keys.create_llm_token", new_callable=AsyncMock
        ) as mock_llm,
        patch(
            "app.api.private_ai_keys.create_vector_db", new_callable=AsyncMock
        ) as mock_vdb,
    ):
        mock_llm.return_value = MagicMock(
            litellm_token="tok",
            litellm_api_url="http://llm",
            owner_id=user.id,
            team_id=None,
        )
        mock_vdb.return_value = MagicMock(
            database_name="db",
            name="test-key",
            database_host="h",
            database_username="u",
            database_password="p",
            owner_id=user.id,
            team_id=None,
        )
        # client fixture sends X-Amazee-Source: frontend automatically
        _post_key(client, token, test_region.id)

    # Direct creation path taken via header bypass — moad not called
    provision_key_calls = [
        call for call in mock_http.post.call_args_list if "provision-key" in str(call)
    ]
    assert provision_key_calls == [], f"Unexpected moad call(s): {provision_key_calls}"
    mock_llm.assert_called_once()


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_teamless_non_admin_with_header_is_still_delegated(
    mock_client_cls, mock_settings, client, db, test_region
):
    """The header must not give a teamless non-admin the direct path.

    Anyone can send X-Amazee-Source. On the direct path a teamless caller
    lands in the teamless defaults and gets a key with the full per-key
    budget and no team to gate it, so this caller is delegated to moad.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db, is_admin=False)
    assert user.team_id is None
    token = _login(client, user)

    litellm_token = "teamless-key-001"
    db.add(
        DBPrivateAIKey(
            name="pre-seeded-key",
            litellm_token=litellm_token,
            litellm_api_url="http://test-llm",
            database_name="db",
            database_host="host",
            database_username="u",
            database_password="p",
            region_id=test_region.id,
        )
    )
    db.commit()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"llm": {"token": litellm_token}, "vectorDb": {"database": "db"}}

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = mock_http

    with patch(
        "app.api.private_ai_keys.create_llm_token", new_callable=AsyncMock
    ) as mock_llm:
        # client fixture sends X-Amazee-Source: frontend automatically
        response = _post_key(client, token, test_region.id)

    assert response.status_code == 200
    # No key was minted on this backend; moad owns the provisioning
    mock_llm.assert_not_called()
    assert "provision-key" in mock_http.post.call_args[0][0]


# ---------------------------------------------------------------------------
# 3. moad unavailable → 503 (no header, Drupal path)
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
def test_no_header_moad_not_configured_returns_503(
    mock_settings, drupal_client, db, test_region
):
    """Without header, if moad is not configured the endpoint returns 503."""
    mock_settings.MOAD_DASHBOARD_API_URL = None
    mock_settings.MOAD_DASHBOARD_API_TOKEN = None

    user = _make_user(db)
    token = _login(drupal_client, user)

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# 4. Idempotency: existing key for name + region is reused (no moad call)
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_existing_key_team_region_reuses_no_moad_call(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A request whose user's team already has a key for the region reuses it.

    This prevents key proliferation when Drupal retries the provisioning call
    (or the same user re-logs-in). moad must NOT be called on the reuse path.
    The match is team + region (name-agnostic): moad stamps a date suffix on
    the stored name, so a name-based match would never fire.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    token = _login(drupal_client, user)

    # Simulate a PRIOR successful provision: a team exists, has a key for
    # this region, and the user is already pinned to it. The user has a
    # team role (ADMIN), matching what /auth/sign-in creates in production —
    # a system role (USER) + non-null team_id would be rejected by RBAC.
    team = DBTeam(name="moad-team", admin_email=user.email, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    existing_key = DBPrivateAIKey(
        name="test-key",  # matches what _post_key sends; moad no longer stamps a date suffix
        litellm_token="existing-token",
        litellm_api_url="http://test-llm",
        database_name="db",
        database_host="host",
        database_username="u",
        database_password="p",
        region_id=test_region.id,
        team_id=team.id,
    )
    db.add(existing_key)
    # Pin the user to the team with a team role — mirrors the post-provision
    # state (sign-in creates ADMIN role; _pin_user_to_key_team sets team_id).
    user.team_id = team.id
    user.role = UserRole.ADMIN
    db.commit()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock()
    mock_client_cls.return_value = mock_http

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 200
    # moad was NOT called — the existing key was reused.
    mock_http.post.assert_not_called()
    # The returned key is the existing one.
    assert response.json()["litellm_token"] == "existing-token"


# ---------------------------------------------------------------------------
# 5. Team pinning: after delegation the user is pinned to the key's team
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_delegation_pins_user_to_key_team(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """After moad delegation the user's team_id is set to the key's team.

    This is the core fix for the "key never returned" bug: without pinning,
    list_private_ai_keys (scoped by user.team_id) would never see the key.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    token = _login(drupal_client, user)

    # The team moad will have created the key under (different from user's).
    moad_team = DBTeam(name="moad-team", admin_email=user.email, is_active=True)
    db.add(moad_team)
    db.commit()
    db.refresh(moad_team)

    litellm_token = "drupal-key-002"
    # Distinct name so idempotency doesn't short-circuit.
    pre_created_key = DBPrivateAIKey(
        name="pre-seeded-key-2",
        litellm_token=litellm_token,
        litellm_api_url="http://test-llm",
        database_name="db",
        database_host="host",
        database_username="u",
        database_password="p",
        region_id=test_region.id,
        team_id=moad_team.id,
    )
    db.add(pre_created_key)
    db.commit()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"llm": {"token": litellm_token}, "vectorDb": {"database": "db"}}
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = mock_http

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 200
    # The user is now pinned to the key's team.
    db.refresh(user)
    assert user.team_id == moad_team.id


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_provisioned_key_token_mismatch_returns_502(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A key found by database_name whose token differs from moad's is not returned."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    token = _login(drupal_client, user)

    key_team = DBTeam(name="key-team", admin_email=user.email, is_active=True)
    db.add(key_team)
    db.commit()
    db.refresh(key_team)
    seeded = _seed_key(db, "pre-seeded-key-3", "token-a", test_region.id, key_team.id)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "llm": {"token": "token-b"},
        "vectorDb": {"database": seeded.database_name},
    }
    mock_client_cls.return_value = _moad_client(AsyncMock(return_value=mock_response))

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 502
    db.refresh(user)
    assert user.team_id != key_team.id


# ---------------------------------------------------------------------------
# 6. Security: a guessed name from a different tenant is NOT reused
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_idempotency_does_not_leak_cross_tenant_key(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A user from a different team can never receive another tenant's key.

    The idempotency lookup is scoped to the requesting user's OWN team
    (current_user.team_id), so cross-tenant leakage is impossible by
    construction — there is no global match to exploit, regardless of the
    name the attacker guesses.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    # Victim tenant: a different human with their own team + key.
    victim_team = DBTeam(
        name="victim-team",
        admin_email="victim+team-abc@example.com",
        is_active=True,
    )
    db.add(victim_team)
    db.commit()
    db.refresh(victim_team)
    victim_key = DBPrivateAIKey(
        name="secret-key",  # the name the attacker will guess
        litellm_token="victim-secret-token",
        litellm_api_url="http://victim-llm",
        database_name="db",
        database_host="host",
        database_username="u",
        database_password="p",
        region_id=test_region.id,
        team_id=victim_team.id,
    )
    db.add(victim_key)
    db.commit()

    # Attacker: a different user, not a member of victim_team.
    attacker = _make_user(db, email="attacker@example.com")
    attacker_token = _login(drupal_client, attacker)

    mock_response = MagicMock()
    mock_response.status_code = 200
    # moad returns a fresh token that resolves to NO local key — the handler
    # will 502, but that's fine: we only assert moad was called (no reuse).
    mock_response.json.return_value = {"llm": {"token": "attacker-fresh-token"}}
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = mock_http

    # Attacker POSTs the victim's key NAME + same region.
    drupal_client.post(
        "/private-ai-keys",
        json={"region_id": test_region.id, "name": "secret-key"},
        headers={"Authorization": f"Bearer {attacker_token}"},
    )

    # The victim's key must NOT be reused: moad WAS called.
    mock_http.post.assert_called()
    # The attacker is NOT pinned to the victim's team.
    db.refresh(attacker)
    assert attacker.team_id != victim_team.id


# ---------------------------------------------------------------------------
# 7. Legacy compatibility: a stored +tag email is still found
# ---------------------------------------------------------------------------


def test_get_user_by_email_falls_back_to_tagged_row(db):
    """A legacy user whose stored email contains a +tag is still lookup-able.

    Regression test for the backward-compat break flagged in review:
    normalization must not orphan pre-existing tagged rows.
    """
    from app.api.users import get_user_by_email

    legacy = DBUser(
        email="legacy+newsletter@example.com",
        hashed_password=get_password_hash("testpassword"),
        is_active=True,
        is_admin=False,
        role=UserRole.DEFAULT,
    )
    db.add(legacy)
    db.commit()
    db.refresh(legacy)

    # Lookup with the exact tagged address finds the legacy row via the
    # exact-match fallback (normalized lookup misses because no canonical
    # base row exists yet).
    found = get_user_by_email(db, "legacy+newsletter@example.com")
    assert found is not None
    assert found.id == legacy.id


# ---------------------------------------------------------------------------
# 8. Advisory lock: overlapping provisioning requests are serialized
# ---------------------------------------------------------------------------


def _seed_key(session, name, token, region_id, team_id=None):
    key = DBPrivateAIKey(
        name=name,
        litellm_token=token,
        litellm_api_url="http://test-llm",
        database_name=f"db-{token}",
        database_host="host",
        database_username="u",
        database_password="p",
        region_id=region_id,
        team_id=team_id,
    )
    session.add(key)
    session.commit()
    session.refresh(key)
    return key


def _moad_client(post):
    """Build the patched httpx.AsyncClient mock with a given ``post`` mock."""
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = post
    return mock_http


def _lock_key(email, region_id):
    return f"{email.lower()}:{region_id}"


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_concurrent_provisioning_calls_moad_once(mock_settings, db, test_region):
    """Two overlapping requests provision once and return the same key.

    The loser waits on the advisory lock, then re-reads committed state and
    finds the winner's key instead of calling moad a second time (which moad
    would reject with a duplicate alias).
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    team = DBTeam(name="moad-team", admin_email=user.email, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)

    litellm_token = "drupal-key-conc"

    async def slow_provision(*args, **kwargs):
        # Hold the lock long enough for the second request to start waiting,
        # then create the key the way moad's callback would.
        await asyncio.sleep(0.3)
        writer = TestingSessionLocal()
        try:
            _seed_key(writer, "test-key", litellm_token, test_region.id, team.id)
        finally:
            writer.close()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"llm": {"token": litellm_token}, "vectorDb": {"database": f"db-{litellm_token}"}}
        return response

    post = AsyncMock(side_effect=slow_provision)

    second_session = TestingSessionLocal()
    try:
        user_in_second_session = second_session.get(DBUser, user.id)
        request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")
        with patch("httpx.AsyncClient", return_value=_moad_client(post)):
            first, second = await asyncio.gather(
                _delegate_to_moad(request, user, db),
                _delegate_to_moad(request, user_in_second_session, second_session),
            )
    finally:
        second_session.close()

    assert post.await_count == 1
    assert first.id == second.id


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_lock_held_elsewhere_returns_503_and_skips_moad(
    mock_settings, monkeypatch, db, test_region
):
    """A request that cannot get the lock in time returns 503 and skips moad."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    monkeypatch.setattr("app.api.private_ai_keys.PROVISION_LOCK_TIMEOUT", "100ms")

    user = _make_user(db)
    post = AsyncMock()

    holder = engine.connect()
    try:
        holder_tx = holder.begin()
        holder.execute(
            text(f"SELECT pg_advisory_xact_lock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        )
        request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")
        with patch("httpx.AsyncClient", return_value=_moad_client(post)):
            with pytest.raises(HTTPException) as exc_info:
                await _delegate_to_moad(request, user, db)
        holder_tx.rollback()
    finally:
        holder.close()

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail == "Key provisioning already in progress, please retry."
    )
    post.assert_not_called()


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_lock_released_after_moad_failure(mock_settings, db, test_region):
    """A failed provisioning frees the lock so a retry can proceed."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    response = MagicMock()
    response.status_code = 500
    response.text = "boom"
    post = AsyncMock(return_value=response)

    request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")
    with patch("httpx.AsyncClient", return_value=_moad_client(post)):
        with pytest.raises(HTTPException) as exc_info:
            await _delegate_to_moad(request, user, db)
    assert exc_info.value.status_code == 502

    conn = engine.connect()
    try:
        acquired = conn.execute(
            text(f"SELECT pg_try_advisory_lock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        ).scalar()
        conn.execute(
            text(f"SELECT pg_advisory_unlock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        )
    finally:
        conn.close()
    assert acquired is True


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_different_region_does_not_contend(
    mock_settings, monkeypatch, db, test_region
):
    """The lock is per region: a busy region does not block another one."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    monkeypatch.setattr("app.api.private_ai_keys.PROVISION_LOCK_TIMEOUT", "100ms")

    user = _make_user(db)
    other_region = DBRegion(
        name="test-region-2",
        label="Test Region 2",
        postgres_host="amazee-test-postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://test-litellm.com",
        litellm_api_key="test-litellm-key",
        is_active=True,
    )
    db.add(other_region)
    db.commit()
    db.refresh(other_region)
    _seed_key(db, "pre-seeded-key", "drupal-key-other-region", other_region.id)

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"llm": {"token": "drupal-key-other-region"}, "vectorDb": {"database": "db-drupal-key-other-region"}}
    post = AsyncMock(return_value=response)

    holder = engine.connect()
    try:
        holder_tx = holder.begin()
        holder.execute(
            text(f"SELECT pg_advisory_xact_lock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        )
        request = PrivateAIKeyCreate(region_id=other_region.id, name="test-key")
        with patch("httpx.AsyncClient", return_value=_moad_client(post)):
            result = await _delegate_to_moad(request, user, db)
        holder_tx.rollback()
    finally:
        holder.close()

    assert result.litellm_token == "drupal-key-other-region"
    post.assert_called_once()


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_name_bypass_still_provisions_under_lock(mock_settings, db, test_region):
    """A different requested name still bypasses idempotency under the lock."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    team = DBTeam(name="moad-team", admin_email=user.email, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    _seed_key(db, "other-name", "existing-token", test_region.id, team.id)

    new_token = "drupal-key-new"

    async def provision(*args, **kwargs):
        writer = TestingSessionLocal()
        try:
            _seed_key(writer, "test-key", new_token, test_region.id, team.id)
        finally:
            writer.close()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"llm": {"token": new_token}, "vectorDb": {"database": "db-drupal-key-new"}}
        return response

    post = AsyncMock(side_effect=provision)

    request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")
    with patch("httpx.AsyncClient", return_value=_moad_client(post)):
        result = await _delegate_to_moad(request, user, db)

    post.assert_called_once()
    assert result.litellm_token == new_token


@pytest.mark.asyncio
@patch("app.api.private_ai_keys.settings")
async def test_cancelled_request_releases_lock(mock_settings, db, test_region):
    """A request cancelled while waiting for the lock does not leak it.

    The thread it left behind acquires the lock anyway, so the connection must
    be closed when the thread finishes; otherwise every later request for this
    user and region would time out with a 503.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    post = AsyncMock()
    request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")

    holder = engine.connect()
    try:
        holder_tx = holder.begin()
        holder.execute(
            text(f"SELECT pg_advisory_xact_lock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        )
        with patch("httpx.AsyncClient", return_value=_moad_client(post)):
            task = asyncio.create_task(_delegate_to_moad(request, user, db))
            # Let the task reach the lock wait inside the thread.
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # The orphaned thread now gets the lock; its connection must be closed.
        holder_tx.rollback()
        await asyncio.sleep(0.5)
    finally:
        holder.close()

    conn = engine.connect()
    try:
        acquired = conn.execute(
            text(f"SELECT pg_try_advisory_lock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        ).scalar()
        conn.execute(
            text(f"SELECT pg_advisory_unlock({PROVISION_LOCK_KEY_SQL})"),
            {"key": _lock_key(user.email, test_region.id)},
        )
    finally:
        conn.close()

    assert acquired is True
    post.assert_not_called()


# ---------------------------------------------------------------------------
# 9. Fallback by admin_email
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("caller_email", ["alice_smith@corp.com", "%@corp.com"])
@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_fallback_escapes_wildcards_in_caller_email(
    mock_client_cls, mock_settings, caller_email, drupal_client, db, test_region
):
    """'_' and '%' in the caller's email must not match another team's admin_email."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    victim_team = DBTeam(
        name="victim-team", admin_email="alice.smith+team-x@corp.com", is_active=True
    )
    db.add(victim_team)
    db.commit()
    db.refresh(victim_team)
    victim_key = _seed_key(
        db, "victim-key", "victim-secret-token", test_region.id, victim_team.id
    )

    caller = _make_user(db, email=caller_email)
    token = _login(drupal_client, caller)
    _seed_key(db, "moad-key", "caller-fresh-token", test_region.id)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "llm": {"token": "caller-fresh-token"},
        "vectorDb": {"database": "db-caller-fresh-token"},
    }
    mock_http = _moad_client(AsyncMock(return_value=mock_response))
    mock_client_cls.return_value = mock_http

    response = drupal_client.post(
        "/private-ai-keys",
        json={"region_id": test_region.id, "name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    mock_http.post.assert_called_once()
    body = response.json()
    assert body["litellm_token"] == "caller-fresh-token"
    assert body["id"] != victim_key.id
    assert "victim-secret-token" not in response.text
    db.refresh(caller)
    assert caller.team_id != victim_team.id


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_fallback_reuses_key_of_own_tagged_team_on_region_switch(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """The base email still finds the key of its own tagged team and pins to it."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    t1 = DBTeam(name="t1", admin_email="alice@corp.com", is_active=True)
    t2 = DBTeam(name="t2", admin_email="alice+team-x@corp.com", is_active=True)
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    _seed_key(db, "t2-key", "t2-token", test_region.id, t2.id)

    caller = _make_user(db, email="alice@corp.com", role=UserRole.ADMIN, team_id=t1.id)
    token = _login(drupal_client, caller)
    mock_http = AsyncMock()
    mock_client_cls.return_value = mock_http

    response = drupal_client.post(
        "/private-ai-keys",
        json={"region_id": test_region.id, "name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["litellm_token"] == "t2-token"
    mock_http.post.assert_not_called()
    db.refresh(caller)
    assert caller.team_id == t2.id


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_mailbox_cap_blocks_moad_provisioning_for_a_dot_variant(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A second Gmail address of the same mailbox gets no new workspace."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db, email="a.b@gmail.com")
    other_team = DBTeam(name="other", admin_email="ab@gmail.com")
    db.add(other_team)
    db.commit()
    other = _make_user(db, email="ab@gmail.com", team_id=other_team.id)
    token = _login(drupal_client, user)

    litellm_token = "drupal-key-mailbox"
    _seed_key(db, "pre-seeded-key", litellm_token, test_region.id)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "llm": {"token": litellm_token},
        "vectorDb": {"database": f"db-{litellm_token}"},
    }
    post = AsyncMock(return_value=mock_response)
    mock_client_cls.return_value = _moad_client(post)

    response = _post_key(drupal_client, token, test_region.id)
    assert response.status_code == 429
    post.assert_not_called()
    assert (
        db.query(DBSignupEvent)
        .filter(DBSignupEvent.endpoint == PROVISION_ENDPOINT)
        .count()
        == 0
    )

    # With the other address gone, the single address passes.
    db.delete(other)
    db.commit()
    response = _post_key(drupal_client, token, test_region.id)
    assert response.status_code == 200
    post.assert_called_once()


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_mailbox_cap_still_returns_an_existing_key(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """The idempotent return runs before the cap, so a key that exists is kept."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    team = DBTeam(name="mailbox-team", admin_email="a.b@gmail.com", is_active=True)
    other_team = DBTeam(name="other", admin_email="ab@gmail.com", is_active=True)
    db.add_all([team, other_team])
    db.commit()
    user = _make_user(db, email="a.b@gmail.com", role=UserRole.ADMIN, team_id=team.id)
    _make_user(db, email="ab@gmail.com", role=UserRole.ADMIN, team_id=other_team.id)
    _seed_key(db, "test-key", "existing-token", test_region.id, team_id=team.id)
    token = _login(drupal_client, user)

    post = AsyncMock()
    mock_client_cls.return_value = _moad_client(post)

    response = _post_key(drupal_client, token, test_region.id)

    assert response.status_code == 200
    assert response.json()["litellm_token"] == "existing-token"
    post.assert_not_called()


def _moad_ok(db, region_id, litellm_token):
    """Seed the key that moad would create and a moad mock that returns it."""
    _seed_key(db, "pre-seeded-key", litellm_token, region_id)
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "llm": {"token": litellm_token},
        "vectorDb": {"database": f"db-{litellm_token}"},
    }
    return AsyncMock(return_value=response)


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_registered_user_without_team_gets_403(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A /auth/register user (password, no team) gets no moad workspace."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    user = _make_user(db, password="testpassword")
    token = _login(drupal_client, user)
    post = _moad_ok(db, test_region.id, "registered-token")
    mock_client_cls.return_value = _moad_client(post)

    response = _post_key(drupal_client, token, test_region.id)
    assert response.status_code == 403
    post.assert_not_called()

    # The same teamless user without a password is not refused by this check.
    user.hashed_password = None
    db.commit()
    response = _post_key(drupal_client, token, test_region.id)
    assert response.status_code == 200
    post.assert_called_once()


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_email_code_user_with_team_still_provisions(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    team = DBTeam(name="code-team", admin_email=EMAIL, is_active=True)
    db.add(team)
    db.commit()
    user = _make_user(db, role=UserRole.ADMIN, team_id=team.id)
    token = _login(drupal_client, user)
    post = _moad_ok(db, test_region.id, "team-token")
    mock_client_cls.return_value = _moad_client(post)

    response = _post_key(drupal_client, token, test_region.id)
    assert response.status_code == 200
    post.assert_called_once()


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_registered_user_cannot_take_a_key_by_email_match(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A registered email is not proven, so the admin_email fallback must not run."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    victim_team = DBTeam(name="victim", admin_email="victim+team-abc@example.com")
    db.add(victim_team)
    db.commit()
    _seed_key(db, "victim-key", "victim-token", test_region.id, team_id=victim_team.id)
    attacker = _make_user(db, email="victim@example.com", password="testpassword")
    token = _login(drupal_client, attacker)
    post = AsyncMock()
    mock_client_cls.return_value = _moad_client(post)

    response = drupal_client.post(
        "/private-ai-keys",
        # An empty name counts as a retry, so the fallback would return the key.
        json={"region_id": test_region.id, "name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "victim-token" not in response.text
    db.refresh(attacker)
    assert attacker.team_id is None
    post.assert_not_called()


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_registered_user_gets_a_team_by_code_sign_in(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """The email code proves the inbox, so the 403 for registered users ends."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    user = _make_user(db, password="testpassword")
    post = _moad_ok(db, test_region.id, "registered-team-token")
    mock_client_cls.return_value = _moad_client(post)

    response = _code_sign_in(drupal_client, EMAIL)
    assert response.status_code == 200
    db.refresh(user)
    assert user.team_id is not None
    assert user.role == UserRole.ADMIN
    assert user.hashed_password is not None

    response = _post_key(drupal_client, response.json()["access_token"], test_region.id)
    assert response.status_code == 200
    post.assert_called_once()


def _code_sign_in(client, email):
    """Sign in with an email code; LiteLLM calls are mocked."""
    with (
        patch("app.api.auth.DynamoDBService") as dynamodb,
        patch("app.api.teams.LiteLLMService.create_team", new_callable=AsyncMock),
        patch("app.core.litellm_user_sync.LiteLLMService") as sync_litellm,
    ):
        sync_litellm.return_value = AsyncMock()
        dynamodb.return_value.read_validation_code.return_value = {
            "email": email,
            "code": "TESTCODE",
        }
        return client.post(
            "/auth/sign-in", json={"username": email, "verification_code": "TESTCODE"}
        )


def test_capped_gmail_user_signs_in_without_a_team(drupal_client, db):
    """The mailbox cap stops a second team, not the sign-in of an existing user."""
    other_team = DBTeam(name="other", admin_email="ab@gmail.com", is_active=True)
    db.add(other_team)
    db.commit()
    _make_user(db, email="ab@gmail.com", role=UserRole.ADMIN, team_id=other_team.id)
    user = _make_user(db, email="a.b@gmail.com")

    response = _code_sign_in(drupal_client, "a.b@gmail.com")

    assert response.status_code == 200
    db.refresh(user)
    assert user.team_id is None


def test_sign_in_team_attach_syncs_litellm_membership(drupal_client, db, test_region):
    user = _make_user(db)
    with patch("app.api.auth.sync_add_user_to_team", new_callable=AsyncMock) as sync:
        response = _code_sign_in(drupal_client, EMAIL)

    assert response.status_code == 200
    db.refresh(user)
    assert user.team_id is not None
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["team_id"] == user.team_id


def test_sign_in_team_attach_is_undone_when_litellm_sync_fails(
    drupal_client, db, test_region
):
    """A failed sync leaves the user signed in without the team."""
    user = _make_user(db)
    with patch(
        "app.api.auth.sync_add_user_to_team",
        new_callable=AsyncMock,
        side_effect=RuntimeError("litellm down"),
    ):
        response = _code_sign_in(drupal_client, EMAIL)

    assert response.status_code == 200
    db.refresh(user)
    assert user.team_id is None
    assert user.role == UserRole.DEFAULT
    team_ids = {t.id for t in db.query(DBTeam).filter(DBTeam.admin_email == EMAIL)}
    assert len(team_ids) == 1

    # The next sign-in reuses the team that the failed sync left without members.
    with patch("app.api.auth.sync_add_user_to_team", new_callable=AsyncMock):
        response = _code_sign_in(drupal_client, EMAIL)

    assert response.status_code == 200
    db.refresh(user)
    assert user.team_id in team_ids
    assert db.query(DBTeam).filter(DBTeam.admin_email == EMAIL).count() == 1


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_fallback_skips_team_with_another_persons_member(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A removed admin keeps the admin_email but must not get the team's key."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    team = DBTeam(name="old-team", admin_email="alice+team-x@corp.com", is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    _make_user(db, email="bob@corp.com", role=UserRole.ADMIN, team_id=team.id)
    _seed_key(db, "team-key", "team-secret-token", test_region.id, team.id)

    removed_admin = _make_user(db, email="alice@corp.com")
    token = _login(drupal_client, removed_admin)
    post = _moad_ok(db, test_region.id, "alice-fresh-token")
    mock_client_cls.return_value = _moad_client(post)

    response = drupal_client.post(
        "/private-ai-keys",
        json={"region_id": test_region.id, "name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert "team-secret-token" not in response.text
    post.assert_called_once()
    db.refresh(removed_admin)
    assert removed_admin.team_id != team.id


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_fallback_reuses_key_of_shared_workspace_on_region_switch(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """moad adds each workspace member as "<email>+<team id>@". The owner's own
    tagged member keeps the team reusable, even with another person in it."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"
    t1 = DBTeam(name="t1", admin_email="alice@corp.com", is_active=True)
    t2 = DBTeam(name="t2", admin_email="alice+team-x@corp.com", is_active=True)
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    _make_user(db, email=f"alice+{t2.id}@corp.com", role=UserRole.ADMIN, team_id=t2.id)
    _make_user(db, email=f"bob+{t2.id}@corp.com", team_id=t2.id)
    _seed_key(db, "t2-key", "t2-token", test_region.id, t2.id)

    caller = _make_user(db, email="alice@corp.com", role=UserRole.ADMIN, team_id=t1.id)
    token = _login(drupal_client, caller)
    mock_http = AsyncMock()
    mock_client_cls.return_value = mock_http

    response = drupal_client.post(
        "/private-ai-keys",
        json={"region_id": test_region.id, "name": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["litellm_token"] == "t2-token"
    mock_http.post.assert_not_called()
    db.refresh(caller)
    assert caller.team_id == t2.id
