"""
Tests for scripts/drop_legacy_internal_teams.py

Validates team identification, summary generation, and soft-delete behaviour
without touching LiteLLM (all external calls are mocked).
"""

import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.orm import Session

from app.db.models import (
    DBTeam,
    DBTeamProduct,
    DBUser,
    DBPrivateAIKey,
    DBProduct,
)

# Import the functions under test — the script lives one level up from tests/
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
)
from drop_legacy_internal_teams import (
    find_internal_teams,
    get_team_summary,
    drop_teams,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_team(db: Session, **overrides) -> DBTeam:
    defaults = dict(
        name="Test Team",
        admin_email="admin@amazee.io",
        is_active=True,
        created_at=datetime.now(UTC) - timedelta(days=90),
    )
    defaults.update(overrides)
    team = DBTeam(**defaults)
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


# ── find_internal_teams ──────────────────────────────────────────────────


def test_find_internal_teams_matches_amazee_email(db: Session, test_region):
    """Teams with @amazee.io admin email are returned."""
    internal = _make_team(db, name="Hank Team", admin_email="hank@amazee.io")
    _make_team(db, name="External Team", admin_email="someone@customer.com")

    result = find_internal_teams(db)
    assert len(result) == 1
    assert result[0].id == internal.id


def test_find_internal_teams_excludes_already_deleted(db: Session, test_region):
    """Soft-deleted teams are not returned."""
    _make_team(
        db,
        name="Old Team",
        admin_email="old@amazee.io",
        deleted_at=datetime.now(UTC) - timedelta(days=5),
    )

    result = find_internal_teams(db)
    assert len(result) == 0


def test_find_internal_teams_by_explicit_ids(db: Session, test_region):
    """When team_ids are provided, only those teams are returned."""
    t1 = _make_team(db, name="Team A", admin_email="a@amazee.io")
    t2 = _make_team(db, name="Team B", admin_email="b@amazee.io")
    _make_team(db, name="Team C", admin_email="c@amazee.io")

    result = find_internal_teams(db, team_ids=[t1.id, t2.id])
    ids = {t.id for t in result}
    assert ids == {t1.id, t2.id}


def test_find_internal_teams_custom_email_pattern(db: Session, test_region):
    """Custom email pattern filters correctly."""
    _make_team(db, name="Partner", admin_email="ops@partner.dev")
    _make_team(db, name="Internal", admin_email="test@amazee.io")

    result = find_internal_teams(db, email_pattern="@partner.dev")
    assert len(result) == 1
    assert result[0].name == "Partner"


# ── get_team_summary ────────────────────────────────────────────────────


def test_get_team_summary_counts(db: Session, test_region):
    """Summary includes correct user/key/product counts."""
    team = _make_team(db, name="Summary Team", admin_email="sum@amazee.io")

    # Add users
    u1 = DBUser(email="u1@amazee.io", team_id=team.id)
    u2 = DBUser(email="u2@amazee.io", team_id=team.id)
    db.add_all([u1, u2])
    db.commit()

    # Add a team-owned key
    k1 = DBPrivateAIKey(
        name="team-key",
        team_id=team.id,
        region_id=test_region.id,
        litellm_token="tok-1",
    )
    db.add(k1)
    db.commit()

    summary = get_team_summary(db, team)
    assert summary["user_count"] == 2
    assert summary["total_keys"] >= 1
    assert summary["product_ids"] == []


# ── drop_teams ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("drop_legacy_internal_teams.soft_delete_team", new_callable=AsyncMock)
async def test_drop_teams_dry_run_does_not_delete(mock_soft_delete, db: Session):
    """Dry run should NOT call soft_delete_team."""
    team = _make_team(db, name="Dry Run", admin_email="dry@amazee.io")

    await drop_teams(db, [team], dry_run=True)

    mock_soft_delete.assert_not_called()


@pytest.mark.asyncio
@patch("drop_legacy_internal_teams.soft_delete_team", new_callable=AsyncMock)
async def test_drop_teams_execute_calls_soft_delete(mock_soft_delete, db: Session):
    """With execute, soft_delete_team is called for each team."""
    team = _make_team(db, name="Execute", admin_email="exec@amazee.io")

    await drop_teams(db, [team], dry_run=False)

    mock_soft_delete.assert_called_once_with(db, team)


@pytest.mark.asyncio
@patch("drop_legacy_internal_teams.soft_delete_team", new_callable=AsyncMock)
async def test_drop_teams_refuses_teams_with_products(
    mock_soft_delete, db: Session
):
    """Teams with active product associations should block execution."""
    team = _make_team(db, name="Has Product", admin_email="prod@amazee.io")
    product = DBProduct(id="prod-1", name="Starter", active=True)
    db.add(product)
    db.commit()
    db.add(DBTeamProduct(team_id=team.id, product_id=product.id))
    db.commit()

    with pytest.raises(SystemExit) as exc_info:
        await drop_teams(db, [team], dry_run=False)

    assert exc_info.value.code == 1
    mock_soft_delete.assert_not_called()


@pytest.mark.asyncio
@patch("drop_legacy_internal_teams.soft_delete_team", new_callable=AsyncMock)
async def test_drop_teams_allows_inactive_product(mock_soft_delete, db: Session):
    """Teams with only inactive product associations should not be blocked."""
    team = _make_team(db, name="Inactive Prod", admin_email="inact@amazee.io")
    product = DBProduct(id="prod-inactive", name="Old Plan", active=False)
    db.add(product)
    db.commit()
    db.add(DBTeamProduct(team_id=team.id, product_id=product.id))
    db.commit()

    await drop_teams(db, [team], dry_run=False)

    mock_soft_delete.assert_called_once_with(db, team)
