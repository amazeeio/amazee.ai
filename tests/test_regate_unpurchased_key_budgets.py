import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.db.models import DBPoolPurchase, DBPrivateAIKey, DBSpendCap, DBTeam
from app.schemas.models import BudgetType
from scripts.regate_unpurchased_key_budgets import run


def _seed(db, test_region):
    """A gated team that never bought, and one that did. Both hold a key cap."""
    gated = DBTeam(
        name="regate-gated-team",
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    purchased = DBTeam(
        name="regate-purchased-team",
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    db.add_all([gated, purchased])
    db.commit()
    db.add(
        DBPoolPurchase(
            team_id=purchased.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="usd",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pi_regate_{purchased.id}",
            created_at=datetime.now(UTC),
        )
    )
    gated_key = DBPrivateAIKey(
        name="regate-gated-key",
        litellm_token="sk-regate-gated",
        region_id=test_region.id,
        team_id=gated.id,
    )
    purchased_key = DBPrivateAIKey(
        name="regate-purchased-key",
        litellm_token="sk-regate-purchased",
        region_id=test_region.id,
        team_id=purchased.id,
    )
    db.add_all([gated_key, purchased_key])
    db.commit()
    for team, key in ((gated, gated_key), (purchased, purchased_key)):
        db.add(
            DBSpendCap(
                scope="key",
                region_id=test_region.id,
                team_id=team.id,
                key_id=key.id,
                max_budget=99.0,
                budget_duration=None,
            )
        )
    db.commit()
    return gated_key, purchased_key


@patch("scripts.regate_unpurchased_key_budgets.LiteLLMService")
@patch("scripts.regate_unpurchased_key_budgets.SessionLocal")
def test_regate_resets_only_the_unpurchased_key(
    mock_session, mock_litellm, db, test_region
):
    gated_key, _ = _seed(db, test_region)
    mock_session.return_value = db
    instance = mock_litellm.return_value
    instance.update_key_budget = AsyncMock()

    with patch.object(db, "close", lambda: None):
        assert asyncio.run(run(apply=True)) == 0

    instance.update_key_budget.assert_awaited_once_with(
        litellm_token=gated_key.litellm_token,
        budget_duration=f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d",
        max_budget=0.0,
        clear_max_budget=False,
    )
    # The cap row survives, so the first purchase still applies it.
    cap = (
        db.query(DBSpendCap)
        .filter(DBSpendCap.scope == "key", DBSpendCap.key_id == gated_key.id)
        .first()
    )
    assert cap.max_budget == 99.0


@patch("scripts.regate_unpurchased_key_budgets.LiteLLMService")
@patch("scripts.regate_unpurchased_key_budgets.SessionLocal")
def test_regate_dry_run_writes_nothing(mock_session, mock_litellm, db, test_region):
    _seed(db, test_region)
    mock_session.return_value = db
    instance = mock_litellm.return_value
    instance.update_key_budget = AsyncMock()

    with patch.object(db, "close", lambda: None):
        assert asyncio.run(run(apply=False)) == 0

    instance.update_key_budget.assert_not_awaited()


@patch("scripts.regate_unpurchased_key_budgets.LiteLLMService")
@patch("scripts.regate_unpurchased_key_budgets.SessionLocal")
def test_regate_covers_user_scoped_keys(
    mock_session, mock_litellm, db, test_region, test_team_user
):
    """A user-scoped key's cap row has no team_id; the owner's team decides.

    Joining on the cap row's team_id alone would skip these keys and leave
    their raised budget live.
    """
    gated = DBTeam(
        name="regate-user-scoped-team",
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    db.add(gated)
    db.commit()
    test_team_user.team_id = gated.id
    key = DBPrivateAIKey(
        name="regate-user-key",
        litellm_token="sk-regate-user",
        region_id=test_region.id,
        owner_id=test_team_user.id,
    )
    db.add_all([test_team_user, key])
    db.commit()
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=None,
            user_id=test_team_user.id,
            key_id=key.id,
            max_budget=42.0,
        )
    )
    db.commit()

    mock_session.return_value = db
    instance = mock_litellm.return_value
    instance.update_key_budget = AsyncMock()

    with patch.object(db, "close", lambda: None):
        assert asyncio.run(run(apply=True)) == 0

    instance.update_key_budget.assert_awaited_once()
    assert (
        instance.update_key_budget.await_args.kwargs["litellm_token"]
        == key.litellm_token
    )
    assert instance.update_key_budget.await_args.kwargs["max_budget"] == 0.0


@patch("scripts.regate_unpurchased_key_budgets.LiteLLMService")
@patch("scripts.regate_unpurchased_key_budgets.SessionLocal")
def test_regate_leaves_periodic_teams_alone(
    mock_session, mock_litellm, db, test_region
):
    """require_purchase_for_requests defaults to true on every team.

    Only a POOL team is actually gated, so a PERIODIC team's key must keep the
    budget it is entitled to.
    """
    periodic = DBTeam(
        name="regate-periodic-team",
        budget_type=BudgetType.PERIODIC,
        require_purchase_for_requests=True,
    )
    db.add(periodic)
    db.commit()
    key = DBPrivateAIKey(
        name="regate-periodic-key",
        litellm_token="sk-regate-periodic",
        region_id=test_region.id,
        team_id=periodic.id,
    )
    db.add(key)
    db.commit()
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=periodic.id,
            key_id=key.id,
            max_budget=77.0,
        )
    )
    db.commit()

    mock_session.return_value = db
    instance = mock_litellm.return_value
    instance.update_key_budget = AsyncMock()

    with patch.object(db, "close", lambda: None):
        assert asyncio.run(run(apply=True)) == 0

    instance.update_key_budget.assert_not_awaited()
