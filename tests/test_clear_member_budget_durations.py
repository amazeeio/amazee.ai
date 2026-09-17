import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import false

from app.db.models import DBRegion, DBSpendCap, DBUser
from scripts.clear_member_budget_durations import run


def _seed(db, test_team, test_team_user, test_region):
    other = DBUser(
        email="member-cleanup@example.com",
        hashed_password="x",
        is_active=True,
        is_admin=False,
        role="key_creator",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    db.add(other)
    db.commit()
    for user_id in (test_team_user.id, other.id):
        db.add(
            DBSpendCap(
                scope="team_member",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user_id,
                max_budget=10.0,
                budget_duration="1mo",
            )
        )
    db.commit()
    return other


def _caps(db, test_team):
    return (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.team_id == test_team.id,
        )
        .all()
    )


def _mock_service(mock_litellm, test_team_user):
    mock_instance = mock_litellm.return_value
    # The second seeded member has no LiteLLM membership row on purpose: it is
    # pushed with spend 0.0.
    mock_instance.get_team_info = AsyncMock(
        return_value={
            "team_info": {"spend": 0.0},
            "team_memberships": [{"user_id": str(test_team_user.id), "spend": 4.0}],
        }
    )
    mock_instance.update_team_member = AsyncMock()
    return mock_instance


def test_clear_member_budget_durations_dry_run_writes_nothing(
    db, test_team, test_team_user, test_region, monkeypatch
):
    _seed(db, test_team, test_team_user, test_region)

    with (
        patch("scripts.clear_member_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_member_budget_durations.LiteLLMService") as mock_litellm,
    ):
        # The script closes the session it is given; the test fixture owns this
        # one and still needs it after the run. monkeypatch restores the real
        # close before the fixture teardown, or the session stays open in a
        # transaction and the next test's TRUNCATE waits on its lock forever.
        monkeypatch.setattr(db, "close", lambda: None)
        mock_instance = _mock_service(mock_litellm, test_team_user)

        assert asyncio.run(run(apply=False)) == 0

        mock_instance.update_team_member.assert_not_awaited()
        assert [cap.budget_duration for cap in _caps(db, test_team)] == ["1mo", "1mo"]
        db.rollback()


def test_clear_member_budget_durations_applies_and_is_idempotent(
    db, test_team, test_team_user, test_region, monkeypatch
):
    other = _seed(db, test_team, test_team_user, test_region)
    # A member whose row was already cleared: the script must leave it alone.
    done = DBUser(
        email="member-already-cleared@example.com",
        hashed_password="x",
        is_active=True,
        is_admin=False,
        role="key_creator",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    db.add(done)
    db.commit()
    db.add(
        DBSpendCap(
            scope="team_member",
            region_id=test_region.id,
            team_id=test_team.id,
            user_id=done.id,
            max_budget=10.0,
            budget_duration=None,
        )
    )
    db.commit()

    with (
        patch("scripts.clear_member_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_member_budget_durations.LiteLLMService") as mock_litellm,
    ):
        monkeypatch.setattr(db, "close", lambda: None)
        mock_instance = _mock_service(mock_litellm, test_team_user)
        mock_instance.get_team_info = AsyncMock(
            return_value={
                "team_info": {"spend": 0.0},
                "team_memberships": [
                    {"user_id": str(test_team_user.id), "spend": 4.0},
                    {"user_id": str(done.id), "spend": 7.0},
                ],
            }
        )

        assert asyncio.run(run(apply=True)) == 0

        assert mock_instance.update_team_member.await_count == 2
        by_user = {
            call.kwargs["user_id"]: call.kwargs
            for call in mock_instance.update_team_member.await_args_list
        }
        assert by_user[str(test_team_user.id)]["max_budget_in_team"] == 14.0
        # No membership row, so the ceiling starts at the cap.
        assert by_user[str(other.id)]["max_budget_in_team"] == 10.0
        for kwargs in by_user.values():
            assert kwargs["clear_budget_duration"] is True
            assert "budget_duration" not in kwargs
            assert "spend" not in kwargs
        caps = _caps(db, test_team)
        assert len(caps) == 3
        assert all(cap.budget_duration is None for cap in caps)

        mock_instance.update_team_member.reset_mock()
        assert asyncio.run(run(apply=True)) == 0
        mock_instance.update_team_member.assert_not_awaited()
        db.rollback()


def test_clear_member_budget_durations_keeps_rows_when_a_write_fails(
    db, test_team, test_team_user, test_region, monkeypatch
):
    _seed(db, test_team, test_team_user, test_region)

    with (
        patch("scripts.clear_member_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_member_budget_durations.LiteLLMService") as mock_litellm,
    ):
        monkeypatch.setattr(db, "close", lambda: None)
        mock_instance = _mock_service(mock_litellm, test_team_user)
        mock_instance.update_team_member = AsyncMock(side_effect=RuntimeError("boom"))

        assert asyncio.run(run(apply=True)) == 1

        cap = (
            db.query(DBSpendCap)
            .filter(
                DBSpendCap.scope == "team_member",
                DBSpendCap.user_id == test_team_user.id,
            )
            .first()
        )
        assert cap.budget_duration == "1mo"
        db.rollback()


def test_clear_member_budget_durations_fails_when_region_is_missing(
    db, test_team, test_team_user, test_region, monkeypatch
):
    _seed(db, test_team, test_team_user, test_region)
    # A foreign key keeps the region row alive while a cap points at it, so the
    # missing-region path is reproduced by hiding the row from the query.
    real_query = db.query

    def query_without_regions(model, *args, **kwargs):
        if model is DBRegion:
            return real_query(model, *args, **kwargs).filter(false())
        return real_query(model, *args, **kwargs)

    with (
        patch("scripts.clear_member_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_member_budget_durations.LiteLLMService") as mock_litellm,
    ):
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(db, "query", query_without_regions)
        mock_instance = _mock_service(mock_litellm, test_team_user)

        assert asyncio.run(run(apply=True)) == 1

        mock_instance.get_team_info.assert_not_awaited()
        monkeypatch.setattr(db, "query", real_query)
        assert [cap.budget_duration for cap in _caps(db, test_team)] == ["1mo", "1mo"]
        db.rollback()


def test_clear_member_budget_durations_skips_a_member_whose_user_is_gone(
    db, test_team, test_team_user, test_region, monkeypatch, capsys
):
    _seed(db, test_team, test_team_user, test_region)
    # A foreign key keeps the user row alive while a cap points at it, so the
    # missing-user path is reproduced by hiding the row from the query.
    real_query = db.query

    def query_without_users(model, *args, **kwargs):
        if model is DBUser:
            return real_query(model, *args, **kwargs).filter(false())
        return real_query(model, *args, **kwargs)

    with (
        patch("scripts.clear_member_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_member_budget_durations.LiteLLMService") as mock_litellm,
    ):
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(db, "query", query_without_users)
        mock_instance = _mock_service(mock_litellm, test_team_user)

        assert asyncio.run(run(apply=True)) == 0

        mock_instance.update_team_member.assert_not_awaited()
        out = capsys.readouterr().out
        assert "skipped=2" in out
        assert out.count("[SKIP]") == 2
        monkeypatch.setattr(db, "query", real_query)
        db.rollback()
