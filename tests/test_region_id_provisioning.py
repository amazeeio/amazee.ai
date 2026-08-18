"""
Tests for region_id validation introduced by the AI-on-demand regional provisioning PR.

Coverage:
  - team registration with invalid/missing region_id
  - team registration in a dedicated region (rejected)
  - disassociating a team's primary region (rejected)
  - migration backfill logic for teams.region_id
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models import DBRegion, DBTeam, DBTeamRegion


@pytest.fixture
def mock_dynamodb():
    """Local mock for DynamoDB — mirrors the one in test_auth.py."""
    with patch("app.api.auth.DynamoDBService") as mock:
        mock_instance = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dedicated_region(db, name="dedicated-region-test"):
    region = DBRegion(
        name=name,
        label="Dedicated Region",
        postgres_host="dedicated-host",
        postgres_port=5432,
        postgres_admin_user="admin",
        postgres_admin_password="password",
        litellm_api_url="https://dedicated-litellm.com",
        litellm_api_key="dedicated-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_inactive_region(db, name="inactive-region-test"):
    region = DBRegion(
        name=name,
        label="Inactive Region",
        postgres_host="inactive-host",
        postgres_port=5432,
        postgres_admin_user="admin",
        postgres_admin_password="password",
        litellm_api_url="https://inactive-litellm.com",
        litellm_api_key="inactive-key",
        is_active=False,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


# ---------------------------------------------------------------------------
# 1. Invalid / missing region_id on team registration
# ---------------------------------------------------------------------------


class TestRegisterTeamRegionIdValidation:
    def test_register_team_missing_region_id_returns_422(self, client, admin_token):
        """
        Given a team registration payload without region_id
        When submitted by an admin
        Then it should return 422 Unprocessable Entity (schema validation).
        """
        response = client.post(
            "/teams/",
            json={
                "name": "No Region Team",
                "admin_email": "no-region@example.com",
                "budget_type": "periodic",
                # region_id intentionally omitted
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 422

    def test_register_team_nonexistent_region_id_returns_400(self, client, admin_token):
        """
        Given a region_id that does not exist in the database
        When registering a team
        Then it should return 400 with an informative error.
        """
        response = client.post(
            "/teams/",
            json={
                "name": "Ghost Region Team",
                "admin_email": "ghost-region@example.com",
                "budget_type": "periodic",
                "region_id": 999999,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400
        assert "Invalid or inactive region_id" in response.json()["detail"]

    def test_register_team_inactive_region_returns_400(self, client, admin_token, db):
        """
        Given an existing but inactive region
        When registering a team with that region_id
        Then it should return 400 — inactive regions are not valid.
        """
        inactive = _make_inactive_region(db, "inactive-for-register")
        response = client.post(
            "/teams/",
            json={
                "name": "Inactive Region Team",
                "admin_email": "inactive-region@example.com",
                "budget_type": "periodic",
                "region_id": inactive.id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400
        assert "Invalid or inactive region_id" in response.json()["detail"]

    def test_register_team_with_null_region_id_returns_422(self, client, admin_token):
        """
        Given a payload with region_id explicitly set to null
        When registering a team
        Then it should return 422 — region_id is required, not nullable.
        """
        response = client.post(
            "/teams/",
            json={
                "name": "Null Region Team",
                "admin_email": "null-region@example.com",
                "budget_type": "periodic",
                "region_id": None,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 2. Attempting to register a team in a dedicated region
# ---------------------------------------------------------------------------


class TestRegisterTeamDedicatedRegionRejected:
    def test_register_team_in_dedicated_region_returns_400(
        self, client, admin_token, db
    ):
        """
        Given an active dedicated region
        When registering a new team using that region_id
        Then it should return 400 — dedicated regions cannot host new teams.
        """
        dedicated = _make_dedicated_region(db, "dedicated-signup-block")
        response = client.post(
            "/teams/",
            json={
                "name": "Dedicated Signup Team",
                "admin_email": "dedicated-signup@example.com",
                "budget_type": "periodic",
                "region_id": dedicated.id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400
        assert "dedicated region" in response.json()["detail"].lower()

    def test_sign_in_fails_gracefully_when_no_public_region_exists(
        self, client, db, mock_dynamodb
    ):
        """
        Given no active non-dedicated regions exist in the database
        When a new user tries to sign in for the first time
        Then the endpoint should return 500 with a clear error.
        """
        # Deactivate all existing regions so none are available
        db.query(DBRegion).update({"is_active": False})
        db.commit()

        email = "brand-new-user@example.com"
        code = "TESTCODE"
        mock_dynamodb.read_validation_code.return_value = {
            "email": email,
            "code": code,
            "ttl": 9999999999,
        }

        response = client.post(
            "/auth/sign-in", json={"username": email, "verification_code": code}
        )
        assert response.status_code == 500
        assert "No active public region" in response.json()["detail"]

    def test_sign_in_fails_when_only_dedicated_regions_exist(
        self, client, db, mock_dynamodb
    ):
        """
        Given only dedicated (non-public) regions exist
        When a new user tries to sign in for the first time
        Then the endpoint should return 500 — no valid region to provision into.
        """
        db.query(DBRegion).update({"is_dedicated": True})
        db.commit()

        email = "new-user-dedicated-only@example.com"
        code = "TESTCODE2"
        mock_dynamodb.read_validation_code.return_value = {
            "email": email,
            "code": code,
            "ttl": 9999999999,
        }

        response = client.post(
            "/auth/sign-in", json={"username": email, "verification_code": code}
        )
        assert response.status_code == 500
        assert "No active public region" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 3. Disassociating a team's primary region is blocked
# ---------------------------------------------------------------------------


class TestDisassociatePrimaryRegion:
    def test_disassociate_primary_region_returns_400(
        self, client, admin_token, db, test_team, test_region
    ):
        """
        Given a team whose primary region_id points to test_region
        When an admin tries to disassociate that region
        Then it should return 400 — cannot remove the primary region.
        """
        # Ensure the team has a primary region_id and a team_regions row
        test_team.region_id = test_region.id
        exists = (
            db.query(DBTeamRegion)
            .filter(
                DBTeamRegion.team_id == test_team.id,
                DBTeamRegion.region_id == test_region.id,
            )
            .first()
        )
        if not exists:
            db.add(DBTeamRegion(team_id=test_team.id, region_id=test_region.id))
        db.commit()

        response = client.delete(
            f"/regions/{test_region.id}/teams/{test_team.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 400
        assert "primary region" in response.json()["detail"].lower()

    def test_disassociate_non_primary_region_succeeds(
        self, client, admin_token, db, test_team, test_region
    ):
        """
        Given a team with a primary region (test_region) and an extra secondary region
        When an admin disassociates the secondary region
        Then it should succeed — only the primary region is protected.
        """
        # Set test_region as primary
        test_team.region_id = test_region.id
        db.commit()

        # Create a second, non-primary region and associate it
        extra_region = DBRegion(
            name="extra-non-primary-region",
            label="Extra Region",
            postgres_host="extra-host",
            postgres_port=5432,
            postgres_admin_user="admin",
            postgres_admin_password="password",
            litellm_api_url="https://extra-litellm.com",
            litellm_api_key="extra-key",
            is_active=True,
            is_dedicated=False,
        )
        db.add(extra_region)
        db.commit()
        db.refresh(extra_region)
        db.add(DBTeamRegion(team_id=test_team.id, region_id=extra_region.id))
        db.commit()

        with patch(
            "app.api.regions.sync_remove_user_from_team", new_callable=AsyncMock
        ):
            response = client.delete(
                f"/regions/{extra_region.id}/teams/{test_team.id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert response.status_code == 200
        assert "disassociated" in response.json()["message"].lower()

        # Verify primary region_id is unchanged
        db.refresh(test_team)
        assert test_team.region_id == test_region.id

    def test_disassociate_primary_region_does_not_alter_db(
        self, client, admin_token, db, test_team, test_region
    ):
        """
        Given a rejected disassociation attempt for the primary region
        When the request is denied
        Then team.region_id and team_regions row should be unchanged.
        """
        test_team.region_id = test_region.id
        exists = (
            db.query(DBTeamRegion)
            .filter(
                DBTeamRegion.team_id == test_team.id,
                DBTeamRegion.region_id == test_region.id,
            )
            .first()
        )
        if not exists:
            db.add(DBTeamRegion(team_id=test_team.id, region_id=test_region.id))
        db.commit()

        client.delete(
            f"/regions/{test_region.id}/teams/{test_team.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        db.refresh(test_team)
        assert test_team.region_id == test_region.id
        assoc = (
            db.query(DBTeamRegion)
            .filter(
                DBTeamRegion.team_id == test_team.id,
                DBTeamRegion.region_id == test_region.id,
            )
            .first()
        )
        assert assoc is not None


# ---------------------------------------------------------------------------
# 4. Migration backfill logic (unit tests, no Alembic runner)
# ---------------------------------------------------------------------------


class TestMigrationBackfillLogic:
    """
    These tests validate the same SQL logic the migration uses — without
    running Alembic itself — by reproducing the backfill steps against the
    live test DB schema.
    """

    def test_backfill_picks_non_dedicated_region_over_dedicated(self, db):
        """
        Given a team with two team_regions rows — one dedicated, one not —
        The backfill should prefer the non-dedicated region.
        """
        public_region = DBRegion(
            name="bp-public",
            postgres_host="h",
            postgres_port=5432,
            postgres_admin_user="u",
            postgres_admin_password="p",
            litellm_api_url="http://x",
            litellm_api_key="k",
            is_active=True,
            is_dedicated=False,
        )
        dedicated_region = DBRegion(
            name="bp-dedicated",
            postgres_host="h",
            postgres_port=5432,
            postgres_admin_user="u",
            postgres_admin_password="p",
            litellm_api_url="http://x",
            litellm_api_key="k",
            is_active=True,
            is_dedicated=True,
        )
        db.add_all([public_region, dedicated_region])
        db.commit()
        db.refresh(public_region)
        db.refresh(dedicated_region)

        team = DBTeam(
            name="Backfill Test Team",
            admin_email="backfill-test@example.com",
            is_active=True,
            created_at=datetime.now(UTC),
            budget_type="periodic",
            region_id=None,
        )
        db.add(team)
        db.commit()
        db.refresh(team)

        # Add both associations (dedicated first to test ordering preference)
        db.add(DBTeamRegion(team_id=team.id, region_id=dedicated_region.id))
        db.add(DBTeamRegion(team_id=team.id, region_id=public_region.id))
        db.commit()

        # Simulate migration backfill SQL (prefer non-dedicated)
        from sqlalchemy import text

        db.execute(
            text("""
                UPDATE teams
                SET region_id = (
                    SELECT tr.region_id
                    FROM team_regions tr
                    JOIN regions r ON r.id = tr.region_id
                    WHERE tr.team_id = :team_id
                    ORDER BY r.is_dedicated ASC, tr.created_at ASC
                    LIMIT 1
                )
                WHERE id = :team_id AND region_id IS NULL
            """),
            {"team_id": team.id},
        )
        db.commit()
        db.refresh(team)

        assert team.region_id == public_region.id

    def test_backfill_uses_dedicated_if_only_option(self, db):
        """
        Given a team with only a dedicated team_region row
        The backfill should still assign it (it's the only option).
        """
        only_dedicated = DBRegion(
            name="bp-only-dedicated",
            postgres_host="h",
            postgres_port=5432,
            postgres_admin_user="u",
            postgres_admin_password="p",
            litellm_api_url="http://x",
            litellm_api_key="k",
            is_active=True,
            is_dedicated=True,
        )
        db.add(only_dedicated)
        db.commit()
        db.refresh(only_dedicated)

        team = DBTeam(
            name="Only Dedicated Team",
            admin_email="only-dedicated@example.com",
            is_active=True,
            created_at=datetime.now(UTC),
            budget_type="periodic",
            region_id=None,
        )
        db.add(team)
        db.commit()
        db.refresh(team)
        db.add(DBTeamRegion(team_id=team.id, region_id=only_dedicated.id))
        db.commit()

        from sqlalchemy import text

        db.execute(
            text("""
                UPDATE teams
                SET region_id = (
                    SELECT tr.region_id
                    FROM team_regions tr
                    JOIN regions r ON r.id = tr.region_id
                    WHERE tr.team_id = :team_id
                    ORDER BY r.is_dedicated ASC, tr.created_at ASC
                    LIMIT 1
                )
                WHERE id = :team_id AND region_id IS NULL
            """),
            {"team_id": team.id},
        )
        db.commit()
        db.refresh(team)

        assert team.region_id == only_dedicated.id

    def test_backfill_skips_teams_that_already_have_region_id(self, db, test_region):
        """
        Given a team that already has region_id set
        The backfill UPDATE should not overwrite it.
        """
        pre_assigned = DBRegion(
            name="bp-pre-assigned",
            postgres_host="h",
            postgres_port=5432,
            postgres_admin_user="u",
            postgres_admin_password="p",
            litellm_api_url="http://x",
            litellm_api_key="k",
            is_active=True,
            is_dedicated=False,
        )
        db.add(pre_assigned)
        db.commit()
        db.refresh(pre_assigned)

        team = DBTeam(
            name="Pre-Assigned Region Team",
            admin_email="pre-assigned@example.com",
            is_active=True,
            created_at=datetime.now(UTC),
            budget_type="periodic",
            region_id=test_region.id,  # already set
        )
        db.add(team)
        db.commit()
        db.refresh(team)
        db.add(DBTeamRegion(team_id=team.id, region_id=pre_assigned.id))
        db.commit()

        from sqlalchemy import text

        db.execute(
            text("""
                UPDATE teams
                SET region_id = (
                    SELECT tr.region_id
                    FROM team_regions tr
                    JOIN regions r ON r.id = tr.region_id
                    WHERE tr.team_id = :team_id
                    ORDER BY r.is_dedicated ASC, tr.created_at ASC
                    LIMIT 1
                )
                WHERE id = :team_id AND region_id IS NULL
            """),
            {"team_id": team.id},
        )
        db.commit()
        db.refresh(team)

        # Should NOT have been overwritten
        assert team.region_id == test_region.id

    def test_backfill_team_with_no_associations_stays_null_before_fallback(self, db):
        """
        Given a team with zero team_regions rows
        After the first backfill pass (which only touches teams with associations)
        The team.region_id should still be NULL — the fallback step handles it.
        """
        team = DBTeam(
            name="No Assoc Team",
            admin_email="no-assoc@example.com",
            is_active=True,
            created_at=datetime.now(UTC),
            budget_type="periodic",
            region_id=None,
        )
        db.add(team)
        db.commit()
        db.refresh(team)

        from sqlalchemy import text

        db.execute(
            text("""
                UPDATE teams
                SET region_id = (
                    SELECT tr.region_id
                    FROM team_regions tr
                    JOIN regions r ON r.id = tr.region_id
                    WHERE tr.team_id = :team_id
                    ORDER BY r.is_dedicated ASC, tr.created_at ASC
                    LIMIT 1
                )
                WHERE id = :team_id AND region_id IS NULL
            """),
            {"team_id": team.id},
        )
        db.commit()
        db.refresh(team)

        # First pass leaves it NULL — fallback would kick in next
        assert team.region_id is None

    def test_backfill_fallback_assigns_first_public_region(self, db):
        """
        Given a team with no team_regions rows and a public region in the DB
        The fallback backfill step should assign that public region and create
        a team_regions row.
        """
        fallback_region = DBRegion(
            name="bp-fallback-public",
            postgres_host="h",
            postgres_port=5432,
            postgres_admin_user="u",
            postgres_admin_password="p",
            litellm_api_url="http://x",
            litellm_api_key="k",
            is_active=True,
            is_dedicated=False,
        )
        db.add(fallback_region)
        db.commit()
        db.refresh(fallback_region)

        team = DBTeam(
            name="Fallback Region Team",
            admin_email="fallback-region@example.com",
            is_active=True,
            created_at=datetime.now(UTC),
            budget_type="periodic",
            region_id=None,
        )
        db.add(team)
        db.commit()
        db.refresh(team)

        from sqlalchemy import text

        # Replicate the fallback step of the migration
        fallback_row = db.execute(
            text(
                "SELECT id FROM regions"
                " WHERE is_active = true AND is_dedicated = false"
                " ORDER BY id ASC LIMIT 1"
            )
        ).fetchone()
        assert fallback_row is not None, "Need at least one active public region"
        fallback_id = fallback_row[0]

        db.execute(
            text("""
                INSERT INTO team_regions (team_id, region_id, created_at)
                SELECT id, :fallback_id, NOW()
                FROM teams
                WHERE region_id IS NULL
                ON CONFLICT DO NOTHING
            """),
            {"fallback_id": fallback_id},
        )
        db.execute(
            text("UPDATE teams SET region_id = :fallback_id WHERE region_id IS NULL"),
            {"fallback_id": fallback_id},
        )
        db.commit()
        db.refresh(team)

        assert team.region_id == fallback_id

        # A team_regions row should have been created too
        assoc = (
            db.query(DBTeamRegion)
            .filter(
                DBTeamRegion.team_id == team.id,
                DBTeamRegion.region_id == fallback_id,
            )
            .first()
        )
        assert assoc is not None

    def test_register_team_persists_region_id(
        self, client, admin_token, db, test_region
    ):
        """
        Given a valid region
        When a team is registered via the API
        Then team.region_id should be persisted in the DB.
        """
        with patch("app.api.teams.LiteLLMService.create_team", new_callable=AsyncMock):
            response = client.post(
                "/teams/",
                json={
                    "name": "Region Persist Team",
                    "admin_email": "region-persist@example.com",
                    "budget_type": "periodic",
                    "region_id": test_region.id,
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert response.status_code == 201
        team_id = response.json()["id"]
        assert response.json()["region_id"] == test_region.id

        db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        assert db_team is not None
        assert db_team.region_id == test_region.id
