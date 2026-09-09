"""
Tests for Drupal-origin moad delegation via X-Amazee-Source header.

POST /private-ai-keys delegates to moad by default (no header = Drupal).
When X-Amazee-Source: frontend is present the direct creation path is used.
"""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.private_ai_keys import _delegate_to_moad
from app.db.models import DBPrivateAIKey, DBRegion, DBUser, DBTeam
from app.core.security import get_password_hash
from app.core.roles import UserRole
from app.schemas.models import PrivateAIKeyCreate
from tests.conftest import TestingSessionLocal, engine

EMAIL = "test-drupal@example.com"


def _make_user(db, email=EMAIL, role=UserRole.DEFAULT, is_admin=False):
    user = DBUser(
        email=email,
        hashed_password=get_password_hash("testpassword"),
        is_active=True,
        is_admin=is_admin,
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, user):
    resp = client.post(
        "/auth/login",
        data={"username": user.email, "password": "testpassword"},
    )
    return resp.json()["access_token"]


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
    # litellm_token lookup that follows the moad call.
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
    mock_response.json.return_value = {"llm": {"token": litellm_token}}

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
# 2. With header → direct creation (frontend / admin path)
# ---------------------------------------------------------------------------


@patch("app.api.private_ai_keys.settings")
@patch("httpx.AsyncClient")
def test_with_header_bypasses_moad(
    mock_client_cls, mock_settings, client, db, test_region
):
    """A non-admin DEFAULT user with X-Amazee-Source header must NOT be delegated.

    This specifically exercises the header bypass path, not the admin exemption.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    # Deliberately non-admin DEFAULT user — bypass must come from the header,
    # not the is_admin exemption.
    user = _make_user(db, is_admin=False)
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
    mock_response.json.return_value = {"llm": {"token": litellm_token}}
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
        response.json.return_value = {"llm": {"token": litellm_token}}
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
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
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
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": _lock_key(user.email, test_region.id)},
        ).scalar()
        conn.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
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
    response.json.return_value = {"llm": {"token": "drupal-key-other-region"}}
    post = AsyncMock(return_value=response)

    holder = engine.connect()
    try:
        holder_tx = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
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
        response.json.return_value = {"llm": {"token": new_token}}
        return response

    post = AsyncMock(side_effect=provision)

    request = PrivateAIKeyCreate(region_id=test_region.id, name="test-key")
    with patch("httpx.AsyncClient", return_value=_moad_client(post)):
        result = await _delegate_to_moad(request, user, db)

    post.assert_called_once()
    assert result.litellm_token == new_token
