from datetime import UTC, datetime

from app.core.spend_period_service import upsert_team_spend_period
from app.db.models import DBTeamSpendPeriod, DBTeamSpendPeriodKey


def test_upsert_team_spend_period_creates_parent_and_keys(
    db, test_team, test_region, test_team_user
):
    period_start = datetime(2026, 4, 1, tzinfo=UTC)
    period_end = datetime(2026, 5, 1, tzinfo=UTC)

    snapshot = {
        "total_spend": 12.34,
        "total_budget": 50.0,
        "total_prompt_tokens": 100,
        "total_completion_tokens": 200,
        "total_tokens": 300,
        "keys": [
            {
                "key_id": None,
                "owner_id": None,
                "key_name_snapshot": "k1",
                "spend": 10.0,
                "max_budget": 25.0,
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
            {
                "key_id": None,
                "owner_id": test_team_user.id,
                "key_name_snapshot": "k2",
                "spend": 2.34,
                "max_budget": None,
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            },
        ],
    }

    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot,
        stripe_event_id="evt_1",
        subscription_remaining_cents=120,
        topup_remaining_cents=30,
        desired_remaining_cents=150,
    )
    db.commit()

    row = (
        db.query(DBTeamSpendPeriod)
        .filter(
            DBTeamSpendPeriod.team_id == test_team.id,
            DBTeamSpendPeriod.region_id == test_region.id,
            DBTeamSpendPeriod.period_start == period_start,
            DBTeamSpendPeriod.period_end == period_end,
        )
        .first()
    )
    assert row is not None
    assert row.total_spend == 12.34
    assert row.subscription_remaining_cents == 120
    assert row.topup_remaining_cents == 30
    assert row.desired_remaining_cents == 150

    keys = (
        db.query(DBTeamSpendPeriodKey)
        .filter(DBTeamSpendPeriodKey.team_spend_period_id == row.id)
        .all()
    )
    assert len(keys) == 2


def test_upsert_team_spend_period_keeps_original_snapshot_for_same_window(
    db, test_team, test_region
):
    period_start = datetime(2026, 4, 1, tzinfo=UTC)
    period_end = datetime(2026, 5, 1, tzinfo=UTC)

    snapshot1 = {"total_spend": 5.0, "keys": []}
    snapshot2 = {"total_spend": 7.0, "keys": []}

    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot1,
    )
    upsert_team_spend_period(
        db=db,
        team=test_team,
        region_id=test_region.id,
        period_start=period_start,
        period_end=period_end,
        source="test",
        snapshot=snapshot2,
    )
    db.commit()

    rows = (
        db.query(DBTeamSpendPeriod)
        .filter(
            DBTeamSpendPeriod.team_id == test_team.id,
            DBTeamSpendPeriod.region_id == test_region.id,
            DBTeamSpendPeriod.period_start == period_start,
            DBTeamSpendPeriod.period_end == period_end,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].total_spend == 5.0
