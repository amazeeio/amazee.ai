import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.db.models import DBPoolPurchase, DBPrivateAIKey, DBSpendCap, DBTeam
from app.schemas.models import BudgetType
from app.services.litellm import hash_litellm_token
from scripts.clear_key_budget_durations import run


def _seed(db, test_region):
    purchased = DBTeam(
        name="purchased-pool-team",
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    gated = DBTeam(
        name="gated-pool-team",
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    db.add_all([purchased, gated])
    db.commit()
    db.add(
        DBPoolPurchase(
            team_id=purchased.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="usd",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pi_cleanup_{purchased.id}",
            created_at=datetime.now(UTC),
        )
    )
    purchased_key = DBPrivateAIKey(
        name="purchased-key",
        litellm_token="sk-purchased",
        region_id=test_region.id,
        team_id=purchased.id,
    )
    gated_key = DBPrivateAIKey(
        name="gated-key",
        litellm_token="sk-gated",
        region_id=test_region.id,
        team_id=gated.id,
    )
    db.add_all([purchased_key, gated_key])
    db.commit()
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=purchased.id,
            key_id=purchased_key.id,
            max_budget=25.0,
            budget_duration="31d",
        )
    )
    db.commit()
    return purchased_key, gated_key


def test_clear_key_budget_durations_skips_gated_and_is_idempotent(db, test_region):
    purchased_key, gated_key = _seed(db, test_region)
    snapshot = {
        hash_litellm_token(purchased_key.litellm_token): {"budget_duration": "1mo"},
        hash_litellm_token(gated_key.litellm_token): {"budget_duration": "1mo"},
    }

    with (
        patch("scripts.clear_key_budget_durations.SessionLocal", return_value=db),
        patch("scripts.clear_key_budget_durations.LiteLLMService") as mock_litellm,
    ):
        # The script closes the session it is given; the test fixture owns this
        # one and still needs it after the run.
        db.close = lambda: None
        mock_instance = mock_litellm.return_value
        mock_instance.list_all_keys = AsyncMock(return_value=snapshot)
        mock_instance.update_key_budget = AsyncMock()

        assert asyncio.run(run(apply=True)) == 0

        mock_instance.update_key_budget.assert_awaited_once()
        kwargs = mock_instance.update_key_budget.await_args.kwargs
        assert kwargs["litellm_token"] == purchased_key.litellm_token
        assert kwargs["budget_duration"] is None
        assert kwargs["clear_budget_duration"] is True
        assert "max_budget" not in kwargs

        cap = (
            db.query(DBSpendCap)
            .filter(DBSpendCap.key_id == purchased_key.id)
            .first()
        )
        assert cap.budget_duration is None

        # Second run: LiteLLM now reports no duration, so nothing is left to do.
        snapshot[hash_litellm_token(purchased_key.litellm_token)] = {
            "budget_duration": None
        }
        mock_instance.update_key_budget.reset_mock()
        assert asyncio.run(run(apply=True)) == 0
        mock_instance.update_key_budget.assert_not_awaited()
