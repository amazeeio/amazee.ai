"""
Tests for Drupal-origin moad delegation via X-Amazee-Source header.

POST /private-ai-keys delegates to moad by default (no header = Drupal).
When X-Amazee-Source: frontend is present the direct creation path is used.
"""

from unittest.mock import patch, MagicMock, AsyncMock

from app.db.models import DBPrivateAIKey, DBUser
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
    pre_created_key = DBPrivateAIKey(
        name="test-key",
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
    """A request with X-Amazee-Source: frontend must NOT be delegated."""
    mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
    mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

    user = _make_user(db, is_admin=True)
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
        _post_key(client, token, test_region.id)

    # Direct creation path taken — moad provision-key not called
    provision_key_calls = [
        call for call in mock_http.post.call_args_list if "provision-key" in str(call)
    ]
    assert provision_key_calls == []
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
