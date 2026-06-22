"""
Tests for Drupal-origin moad delegation via X-Amazee-Source header.

POST /private-ai-keys delegates to moad by default (no header = Drupal).
When X-Amazee-Source: frontend is present the direct creation path is used.
"""

from unittest.mock import patch, MagicMock, AsyncMock

from app.db.models import DBPrivateAIKey, DBUser, DBTeam
from app.core.security import get_password_hash
from app.core.roles import UserRole

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
def test_existing_key_name_region_reuses_no_moad_call(
    mock_client_cls, mock_settings, drupal_client, db, test_region
):
    """A second request with the same name + region reuses the existing key.

    This prevents key proliferation when Drupal retries the provisioning call.
    moad must NOT be called on the reuse path.
    """
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db)
    token = _login(drupal_client, user)

    # Pre-create a key with the SAME name + region the POST will use.
    team = DBTeam(name="moad-team", admin_email=user.email, is_active=True)
    db.add(team)
    db.commit()
    db.refresh(team)
    existing_key = DBPrivateAIKey(
        name="test-key",
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
