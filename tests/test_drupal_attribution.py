"""
Tests for Drupal-origin attribution.

Flow under test:
  1. POST /auth/validate-email  → writes DynamoDB pending marker
  2. POST /auth/sign-in         → promotes marker to user.created_via_drupal = True
  3. POST /private-ai-keys      → delegates to moad when user is flagged

Edge cases:
  - stale / expired marker is ignored
  - fallback to DEFAULT-role delegation still works for non-flagged users
  - system admins bypass delegation even when flagged
"""

from unittest.mock import patch, MagicMock, AsyncMock

from app.db.models import DBUser, DBTeam, DBPrivateAIKey
from app.core.security import get_password_hash
from app.core.roles import UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMAIL = "drupal-user@example.com"


def _make_user(
    db, email=EMAIL, role=UserRole.DEFAULT, is_admin=False, created_via_drupal=False
):
    user = DBUser(
        email=email,
        hashed_password=get_password_hash("irrelevant"),
        is_active=True,
        is_admin=is_admin,
        role=role,
        created_via_drupal=created_via_drupal,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# 1. validate-email writes a pending marker
# ---------------------------------------------------------------------------


class TestValidateEmailWritesPendingMarker:
    def test_writes_marker_on_success(self, client, db):
        """validate-email should call write_drupal_pending for a valid email."""
        with (
            patch("app.api.auth.DynamoDBService") as mock_dynamo_cls,
            patch("app.api.auth.SESService") as mock_ses_cls,
        ):
            mock_dynamo = MagicMock()
            mock_dynamo.write_validation_code.return_value = True
            mock_dynamo.write_drupal_pending.return_value = True
            mock_dynamo_cls.return_value = mock_dynamo

            mock_ses = MagicMock()
            mock_ses.send_email.return_value = True
            mock_ses_cls.return_value = mock_ses

            response = client.post("/auth/validate-email", json={"email": EMAIL})

        assert response.status_code == 200
        mock_dynamo.write_drupal_pending.assert_called_once_with(EMAIL)

    def test_marker_failure_does_not_break_flow(self, client, db):
        """A DynamoDB error writing the marker must not fail the endpoint."""
        with (
            patch("app.api.auth.DynamoDBService") as mock_dynamo_cls,
            patch("app.api.auth.SESService") as mock_ses_cls,
        ):
            mock_dynamo = MagicMock()
            mock_dynamo.write_validation_code.return_value = True
            mock_dynamo.write_drupal_pending.side_effect = Exception("dynamo down")
            mock_dynamo_cls.return_value = mock_dynamo

            mock_ses = MagicMock()
            mock_ses.send_email.return_value = True
            mock_ses_cls.return_value = mock_ses

            response = client.post("/auth/validate-email", json={"email": EMAIL})

        # Must still succeed despite the attribution error
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 2. sign-in promotes marker to durable user flag
# ---------------------------------------------------------------------------


class TestSignInPromotesDrupalFlag:
    def _sign_in(self, client, db, email=EMAIL, *, pending_exists=True):
        """Helper: run POST /auth/sign-in with mocked DynamoDB + SES."""
        with (
            patch("app.api.auth.DynamoDBService") as mock_dynamo_cls,
            patch("app.api.auth.register_team") as mock_register_team,
        ):
            mock_dynamo = MagicMock()
            # Verification code check
            mock_dynamo.read_validation_code.return_value = {"code": "TESTCODE"}
            # Drupal pending marker
            mock_dynamo.read_drupal_pending.return_value = pending_exists
            mock_dynamo.delete_drupal_pending.return_value = True
            mock_dynamo_cls.return_value = mock_dynamo

            # Stub team creation for new users
            fake_team = MagicMock()
            fake_team.id = 99
            mock_register_team.return_value = fake_team

            resp = client.post(
                "/auth/sign-in",
                json={"username": email, "verification_code": "TESTCODE"},
            )
            return resp, mock_dynamo

    def test_existing_user_gets_flagged(self, client, db):
        """An existing user whose pending marker is present should be flagged."""
        user = _make_user(db)
        assert user.created_via_drupal is False

        response, mock_dynamo = self._sign_in(client, db, pending_exists=True)

        assert response.status_code == 200
        db.refresh(user)
        assert user.created_via_drupal is True
        mock_dynamo.delete_drupal_pending.assert_called_once_with(EMAIL)

    def test_new_user_gets_flagged(self, client, db):
        """A brand-new user created during sign-in should also be flagged."""
        # Create a real team so the FK constraint is satisfied when sign-in
        # auto-creates the user record.

        team = DBTeam(
            name="Auto Team",
            admin_email=EMAIL,
            phone="",
            billing_address="",
        )
        db.add(team)
        db.commit()
        db.refresh(team)

        with (
            patch("app.api.auth.DynamoDBService") as mock_dynamo_cls,
            patch("app.api.auth.register_team") as mock_register_team,
        ):
            mock_dynamo = MagicMock()
            mock_dynamo.read_validation_code.return_value = {"code": "TESTCODE"}
            mock_dynamo.read_drupal_pending.return_value = True
            mock_dynamo.delete_drupal_pending.return_value = True
            mock_dynamo_cls.return_value = mock_dynamo

            # Return the real team so team_id FK is valid
            mock_register_team.return_value = team

            resp = client.post(
                "/auth/sign-in",
                json={"username": EMAIL, "verification_code": "TESTCODE"},
            )

        assert resp.status_code == 200
        user = db.query(DBUser).filter(DBUser.email == EMAIL).first()
        assert user is not None
        assert user.created_via_drupal is True

    def test_no_pending_marker_leaves_user_unflagged(self, client, db):
        """Without a pending marker the user flag must remain False."""
        user = _make_user(db)

        response, mock_dynamo = self._sign_in(client, db, pending_exists=False)

        assert response.status_code == 200
        db.refresh(user)
        assert user.created_via_drupal is False
        mock_dynamo.delete_drupal_pending.assert_not_called()

    def test_already_flagged_user_is_idempotent(self, client, db):
        """Calling sign-in again for an already-flagged user should not fail."""
        user = _make_user(db, created_via_drupal=True)

        response, _ = self._sign_in(client, db, pending_exists=True)

        assert response.status_code == 200
        db.refresh(user)
        assert user.created_via_drupal is True  # still True, not double-written


# ---------------------------------------------------------------------------
# 3. private-ai-keys delegates to moad for flagged users
# ---------------------------------------------------------------------------


class TestPrivateAiKeysDelegation:
    """Tests for the delegation logic in POST /private-ai-keys."""

    def _post_key(self, client, token, region_id):
        return client.post(
            "/private-ai-keys",
            json={"region_id": region_id, "name": "test-key"},
            headers={"Authorization": f"Bearer {token}"},
        )

    def _login(self, client, user):
        resp = client.post(
            "/auth/login",
            data={"username": user.email, "password": "irrelevant"},
        )
        return resp.json()["access_token"]

    @patch("app.api.private_ai_keys.settings")
    @patch("httpx.AsyncClient")
    def test_drupal_flagged_user_is_delegated(
        self, mock_client_cls, mock_settings, client, db, test_region
    ):
        """A user with created_via_drupal=True must be delegated to moad."""
        mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
        mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

        user = _make_user(db, created_via_drupal=True)
        token = self._login(client, user)

        litellm_token = "drupal-key-abc"
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

        response = self._post_key(client, token, test_region.id)

        assert response.status_code == 200
        mock_http.post.assert_called_once()
        call_url = mock_http.post.call_args[0][0]
        assert "provision-key" in call_url

    @patch("app.api.private_ai_keys.settings")
    @patch("httpx.AsyncClient")
    def test_non_drupal_default_role_user_is_not_delegated(
        self, mock_client_cls, mock_settings, client, db, test_region
    ):
        """A DEFAULT-role user without the Drupal flag must NOT be delegated."""
        mock_settings.MOAD_DASHBOARD_API_URL = "http://mock-moad"
        mock_settings.MOAD_DASHBOARD_API_TOKEN = "mock-token"

        # created_via_drupal=False, role=DEFAULT — no flag means no delegation
        user = _make_user(db, role=UserRole.DEFAULT, created_via_drupal=False)
        token = self._login(client, user)

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
            self._post_key(client, token, test_region.id)

        # moad must NOT have been called — verify no call went to provision-key
        provision_key_calls = [
            call
            for call in mock_http.post.call_args_list
            if "provision-key" in str(call)
        ]
        assert provision_key_calls == [], (
            f"Unexpected moad call(s): {provision_key_calls}"
        )

    def test_admin_user_bypasses_delegation_even_when_flagged(
        self, client, db, test_region
    ):
        """System admins with created_via_drupal=True must NOT be delegated."""
        user = _make_user(db, is_admin=True, created_via_drupal=True)
        token = self._login(client, user)

        with (
            patch(
                "app.api.private_ai_keys.create_llm_token", new_callable=AsyncMock
            ) as mock_llm,
            patch(
                "app.api.private_ai_keys.create_vector_db", new_callable=AsyncMock
            ) as mock_vdb,
        ):
            # Provide enough of a return value to satisfy the endpoint
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

            self._post_key(client, token, test_region.id)

        # Should reach the direct creation path, not moad
        mock_llm.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Stale / expired marker is ignored
# ---------------------------------------------------------------------------


class TestStalePendingMarker:
    def test_expired_marker_is_not_promoted(self, client, db):
        """An expired DynamoDB marker (past TTL) must not flag the user."""
        user = _make_user(db)

        with (
            patch("app.api.auth.DynamoDBService") as mock_dynamo_cls,
            patch("app.api.auth.register_team") as mock_register_team,
        ):
            mock_dynamo = MagicMock()
            mock_dynamo.read_validation_code.return_value = {"code": "TESTCODE"}
            # Simulate read_drupal_pending honouring TTL and returning False
            mock_dynamo.read_drupal_pending.return_value = False
            mock_dynamo_cls.return_value = mock_dynamo

            mock_register_team.return_value = MagicMock(id=99)

            resp = client.post(
                "/auth/sign-in",
                json={"username": EMAIL, "verification_code": "TESTCODE"},
            )

        assert resp.status_code == 200
        db.refresh(user)
        assert user.created_via_drupal is False
