from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.worker import (
    _record_periodic_payment_direct,
    apply_billing_cycle_for_team,
    reconcile_periodic_team_budget_drift,
)
from app.db.models import (
    DBAuditLog,
    DBPeriodicBudgetLedgerEntry,
    DBPeriodicPayment,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBTeamSpendPeriod,
    DBUser,
)
from app.schemas.models import BudgetType
from app.services.litellm import INFERENCE_ONLY_ROUTES, LiteLLMService


@pytest.mark.asyncio
async def test_record_periodic_payment_direct_subscription(db, test_team):
    record_id = await _record_periodic_payment_direct(
        db,
        team_id=test_team.id,
        transaction_id="txn_subscription_1",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
    )

    assert record_id is not None
    payment = (
        db.query(DBPeriodicPayment).filter(DBPeriodicPayment.id == record_id).first()
    )
    assert payment.stripe_payment_id == "txn_subscription_1"
    assert payment.amount_cents == 1000
    assert payment.payment_type == "subscription"
    assert (
        payment.sync_status == "pending"
    )  # promoted to "success" only after the full pipeline completes


@pytest.mark.asyncio
async def test_record_periodic_payment_direct_idempotency(db, test_team):
    id1 = await _record_periodic_payment_direct(
        db,
        team_id=test_team.id,
        transaction_id="txn_same_1",
        amount_cents=2500,
    )
    id2 = await _record_periodic_payment_direct(
        db,
        team_id=test_team.id,
        transaction_id="txn_same_1",
        amount_cents=2500,
    )

    assert id1 == id2
    assert (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == "txn_same_1")
        .count()
        == 1
    )


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_updates_sync_status_success(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    key = DBPrivateAIKey(
        name="sync-key",
        litellm_token="sync-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_ok",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    db.refresh(payment)
    assert payment.sync_status == "success"
    mock_litellm.update_team_budget.assert_awaited_once()
    team_kwargs = mock_litellm.update_team_budget.await_args.kwargs
    assert "budget_duration" not in team_kwargs
    assert team_kwargs["clear_budget_duration"] is True
    assert team_kwargs["max_budget"] == 100.0
    assert "spend" not in team_kwargs
    mock_litellm.set_key_restrictions.assert_awaited_once()
    key_kwargs = mock_litellm.set_key_restrictions.await_args.kwargs
    assert key_kwargs["budget_amount"] == 100.0
    assert key_kwargs["spend"] == 0.0
    assert key_kwargs["rpm_limit"] == 1000
    assert key_kwargs["duration"] == "31d"
    assert key_kwargs["budget_duration"] is None


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_keeps_key_spend_cap_and_duration(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    """A key with an explicit DBSpendCap keeps its own cap; the cycle reset is ours."""
    from app.db.models import DBSpendCap

    key = DBPrivateAIKey(
        name="capped-key",
        litellm_token="capped-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            key_id=key.id,
            max_budget=25.0,
            budget_duration="7d",
        )
    )
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    now = datetime.now(UTC)
    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=now,
        period_end=now + timedelta(days=31),
    )

    assert errors == []
    mock_litellm.set_key_restrictions.assert_awaited_once()
    key_kwargs = mock_litellm.set_key_restrictions.await_args.kwargs
    assert key_kwargs["budget_amount"] == 25.0
    assert key_kwargs["budget_duration"] is None
    assert key_kwargs["duration"] == "31d"
    assert (
        mock_litellm.update_team_budget.await_args.kwargs["clear_budget_duration"]
        is True
    )


@pytest.mark.asyncio
@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_recreates_missing_litellm_team(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    _mock_slugs,
    db,
    test_team,
    test_region,
):
    user = DBUser(email="recreate-member@example.com", team_id=test_team.id)
    db.add(user)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_missing_team",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Team not found")
    )
    mock_litellm.create_team = AsyncMock()
    mock_litellm.create_user = AsyncMock()
    mock_litellm.add_team_member = AsyncMock()
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    db.refresh(payment)
    assert payment.sync_status == "success"
    assert mock_litellm.create_team.await_args.kwargs["models"] == ["group-a"]
    # Users and memberships are rebuilt with the team, or it would accept no key.
    lite_team_id = LiteLLMService.format_team_id(test_region.name, test_team.id)
    mock_litellm.create_user.assert_awaited_once_with(
        user_id=str(user.id),
        user_email=user.email,
        auto_create_key=False,
    )
    mock_litellm.add_team_member.assert_awaited_once_with(
        team_id=lite_team_id,
        user_id=str(user.id),
    )
    # Spend on a fresh team is zero, so the full budget is applied.
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 100.0


@pytest.mark.asyncio
@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_rebuilds_keys_lost_with_the_team(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    _mock_slugs,
    db,
    test_team,
    test_region,
):
    """A recreated team has no keys, so each key is minted again under its token."""
    key = DBPrivateAIKey(
        name="lost-key",
        litellm_token="lost-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_lost_keys",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    lite_team_id = mock_litellm_class.format_team_id.return_value
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Team not found")
    )
    mock_litellm.create_team = AsyncMock()
    mock_litellm.create_user = AsyncMock()
    mock_litellm.add_team_member = AsyncMock()
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.create_key = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=[HTTPException(status_code=404, detail="Key not found"), None]
    )

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    mock_litellm.create_key.assert_awaited_once()
    create_kwargs = mock_litellm.create_key.await_args.kwargs
    assert create_kwargs["key"] == "lost-token"
    assert create_kwargs["team_id"] == lite_team_id
    assert create_kwargs["apply_limits"] is False
    assert mock_litellm.set_key_restrictions.await_count == 2
    db.refresh(payment)
    assert payment.sync_status == "success"


@pytest.mark.asyncio
@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_fails_when_key_rebuild_fails(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    _mock_slugs,
    db,
    test_team,
    test_region,
):
    """A key we cannot rebuild leaves the team broken, so the sync must say so."""
    db.add(
        DBPrivateAIKey(
            name="lost-key",
            litellm_token="lost-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_key_rebuild_failed",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Team not found")
    )
    mock_litellm.create_team = AsyncMock()
    mock_litellm.create_user = AsyncMock()
    mock_litellm.add_team_member = AsyncMock()
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.create_key = AsyncMock(side_effect=Exception("litellm down"))
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Key not found")
    )

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert len(errors) == 1
    db.refresh(payment)
    assert payment.sync_status == "sync_failed"


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_reports_key_failure(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    """A key update that fails for any other reason still fails the sync."""
    key = DBPrivateAIKey(
        name="live-key",
        litellm_token="live-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_key_404",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=HTTPException(status_code=500, detail="LiteLLM error")
    )

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert len(errors) == 1
    db.refresh(payment)
    assert payment.sync_status == "sync_failed"


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_rebuilds_a_lost_key_on_a_live_team(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    """A key LiteLLM has lost is rebuilt even when its team is still there.

    A team recreate can fail halfway, so the next run sees a healthy team and
    still has to repair its keys.
    """
    db.add(
        DBPrivateAIKey(
            name="lost-key",
            litellm_token="lost-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_lost_key_live_team",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.create_key = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=[HTTPException(status_code=404, detail="Key not found"), None]
    )

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    assert mock_litellm.create_key.await_args.kwargs["key"] == "lost-token"
    assert mock_litellm.create_key.await_args.kwargs["allowed_routes"] is None
    assert mock_litellm.set_key_restrictions.await_count == 2
    db.refresh(payment)
    assert payment.sync_status == "success"


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_rebuilds_a_trial_key_inference_only(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    """A rebuilt trial key keeps the inference-only routes it was minted with."""
    test_team.admin_email = settings.AI_TRIAL_TEAM_EMAIL
    db.add(
        DBPrivateAIKey(
            name="trial-key",
            litellm_token="trial-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_trial_key_rebuild",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.create_key = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=[HTTPException(status_code=404, detail="Key not found"), None]
    )

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    assert (
        mock_litellm.create_key.await_args.kwargs["allowed_routes"]
        == INFERENCE_ONLY_ROUTES
    )


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_carries_over_spend_overage(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    key = DBPrivateAIKey(
        name="carryover-key",
        litellm_token="carryover-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_carryover",
        amount_cents=100,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 1.4}})
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=100,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    mock_litellm.update_team_budget.assert_awaited_once()
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 2.4
    assert "spend" not in mock_litellm.update_team_budget.await_args.kwargs


@pytest.mark.asyncio
@patch("app.core.worker.compute_active_topup_remaining", return_value=0)
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_carries_over_against_current_litellm_budget(
    mock_limit_service,
    mock_litellm_class,
    _mock_topup,
    db,
    test_team,
    test_region,
):
    key = DBPrivateAIKey(
        name="carryover-key-budget-change",
        litellm_token="carryover-token-budget-change",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_carryover_budget_change",
        amount_cents=200,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 1.4, "max_budget": 1.0}}
    )
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=200,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors == []
    mock_litellm.update_team_budget.assert_awaited_once()
    # Projection uses current spend + remaining budget.
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 3.4
    assert "spend" not in mock_litellm.update_team_budget.await_args.kwargs


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.LimitService")
async def test_apply_billing_cycle_for_team_updates_sync_status_failure(
    mock_limit_service,
    mock_litellm_class,
    db,
    test_team,
    test_region,
):
    key = DBPrivateAIKey(
        name="failed-key",
        litellm_token="failed-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_fail",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        999.0,
        1000,
    )
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(side_effect=Exception("LiteLLM down"))
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=test_team.id,
        budget_cents=10000,
        region_id=test_region.id,
        period_start=datetime.now(UTC),
        period_end=datetime.now(UTC) + timedelta(days=31),
        source_payment_id=payment.id,
    )

    assert errors
    db.refresh(payment)
    assert payment.sync_status == "sync_failed"
    assert "LiteLLM down" in payment.error_log
    mock_litellm.update_team_budget.assert_not_awaited()
    mock_litellm.set_key_restrictions.assert_not_awaited()


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.api.subscription._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
def test_subscription_cycle_endpoint_first_cycle(
    mock_capture,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    client,
    admin_token,
    test_team,
    test_region,
):
    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 123

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cycle_first",
            "budget_cents": 10000,
            "team_id": test_team.id,
            "region_id": test_region.id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "team_id": test_team.id,
        "payment_id": 123,
        "budget_dollars": 100.0,
        "idempotent": False,
    }
    mock_capture.assert_not_awaited()
    mock_sync_ledger.assert_awaited_once()
    mock_apply_cycle.assert_awaited_once()
    assert mock_apply_cycle.await_args.kwargs["source_payment_id"] == 123


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.api.subscription._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
def test_subscription_cycle_endpoint_returns_5xx_on_sync_errors(
    mock_capture,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    client,
    admin_token,
    test_team,
    test_region,
):
    """Issue #617B: when apply_billing_cycle_for_team reports LiteLLM sync
    errors, /cycle must return a non-2xx so MOAD's Stripe webhook retries —
    not a silent 200 that strands the team's budget until the next cycle."""
    mock_apply_cycle.return_value = [
        "Failed to update team 1 budget in region test: LiteLLM down"
    ]
    mock_record_payment.return_value = 789

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cycle_sync_fail",
            "budget_cents": 10000,
            "team_id": test_team.id,
            "region_id": test_region.id,
        },
    )

    assert response.status_code == 502
    mock_apply_cycle.assert_awaited_once()


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.api.subscription._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
def test_subscription_cycle_endpoint_existing_cycle_runs_snapshot_and_ledger(
    mock_capture,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            amount_cents=10000,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            effective_period_start=datetime.now(UTC),
            effective_period_end=datetime.now(UTC) + timedelta(days=31),
            expires_at=datetime.now(UTC) + timedelta(days=31),
            is_active=True,
        )
    )
    db.commit()
    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 456

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cycle_repeat",
            "budget_cents": 10000,
            "team_id": test_team.id,
            "region_id": test_region.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["payment_id"] == 456
    mock_capture.assert_awaited_once()
    mock_sync_ledger.assert_awaited_once()
    mock_apply_cycle.assert_awaited_once()


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.api.subscription._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
def test_subscription_cycle_endpoint_first_cycle_is_region_scoped(
    mock_capture,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    other_region = DBRegion(
        name="test-region-secondary",
        label="Test Region Secondary",
        description="Secondary region for region-scoped cycle tests",
        postgres_host="amazee-test-postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://test-litellm-secondary.com",
        litellm_api_key="test-litellm-key-secondary",
        is_active=True,
    )
    db.add(other_region)
    db.commit()

    # Existing subscription cycle in another region should not affect first-cycle
    # behavior for test_region.
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=other_region.id,
            entry_type="subscription",
            amount_cents=10000,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            effective_period_start=datetime.now(UTC),
            effective_period_end=datetime.now(UTC) + timedelta(days=31),
            expires_at=datetime.now(UTC) + timedelta(days=31),
            is_active=True,
        )
    )
    db.commit()

    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 789

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cycle_region_scope",
            "budget_cents": 10000,
            "team_id": test_team.id,
            "region_id": test_region.id,
        },
    )

    assert response.status_code == 200
    assert response.json()["payment_id"] == 789
    mock_capture.assert_not_awaited()
    mock_sync_ledger.assert_awaited_once()
    mock_apply_cycle.assert_awaited_once()


def test_subscription_cycle_endpoint_idempotent(client, admin_token, db, test_team):
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="txn_cycle_done",
        amount_cents=10000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cycle_done",
            "budget_cents": 10000,
            "team_id": test_team.id,
            "region_id": 999,
        },
    )

    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert response.json()["payment_id"] == payment.id


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_endpoint_success(
    mock_litellm_class,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    db.add(
        DBPrivateAIKey(
            name="deactivate-key",
            litellm_token="deactivate-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    db.commit()

    mock_record_payment.return_value = 321
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 7.0, "max_budget": 20.0}}
    )
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_1",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "team_id": test_team.id,
        "payment_id": 321,
        "idempotent": False,
    }
    mock_litellm.update_team_budget.assert_awaited_once()
    team_kwargs = mock_litellm.update_team_budget.await_args.kwargs
    assert team_kwargs["max_budget"] == 0.0
    assert team_kwargs.get("budget_duration") is None
    assert team_kwargs["clear_budget_duration"] is True
    assert "spend" not in team_kwargs
    mock_litellm.set_key_restrictions.assert_awaited_once()
    key_kwargs = mock_litellm.set_key_restrictions.await_args.kwargs
    assert key_kwargs["budget_amount"] == 0.0
    assert key_kwargs["duration"] is None
    assert key_kwargs["budget_duration"] is None
    assert key_kwargs["spend"] == 0.0


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_keeps_key_cap_duration(
    mock_litellm_class,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    key = DBPrivateAIKey(
        name="deactivate-capped-key",
        litellm_token="deactivate-capped-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            user_id=None,
            key_id=key.id,
            max_budget=25.0,
            budget_duration="1mo",
        )
    )
    db.commit()

    mock_record_payment.return_value = 322
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 7.0, "max_budget": 20.0}}
    )
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_key_cap",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    mock_litellm.set_key_restrictions.assert_awaited_once()
    key_kwargs = mock_litellm.set_key_restrictions.await_args.kwargs
    assert key_kwargs["budget_amount"] == 25.0
    assert key_kwargs["budget_duration"] is None
    assert key_kwargs["duration"] is None


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_preserves_active_topup_budget(
    mock_litellm_class,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    db.add(
        DBPrivateAIKey(
            name="deactivate-key-topup",
            litellm_token="deactivate-token-topup",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=None,
            source_invoice_id=None,
            stripe_payment_id="pi_topup_active_1",
            amount_cents=500,
            consumed_cents=100,
            purchased_at=datetime.now(UTC) - timedelta(days=1),
            effective_period_start=None,
            effective_period_end=None,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            rolled_over_from_id=None,
            is_active=True,
        )
    )
    db.commit()

    mock_record_payment.return_value = 654
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 6.0, "max_budget": 22.0}}
    )
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_with_topup",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    assert response.json()["payment_id"] == 654
    mock_litellm.update_team_budget.assert_awaited_once()
    team_kwargs = mock_litellm.update_team_budget.await_args.kwargs
    assert team_kwargs["max_budget"] == 10.0
    assert team_kwargs.get("budget_duration") is None
    assert team_kwargs["clear_budget_duration"] is True
    assert "spend" not in team_kwargs
    mock_litellm.set_key_restrictions.assert_awaited_once()
    key_kwargs = mock_litellm.set_key_restrictions.await_args.kwargs
    assert key_kwargs["budget_amount"] == 4.0
    assert key_kwargs["duration"] is None
    assert key_kwargs["budget_duration"] is None
    assert key_kwargs["spend"] == 0.0


@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_fails_when_spend_read_fails(
    mock_litellm_class,
    mock_record_payment,
    mock_capture_spend,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    """With top-up left and no readable spend, deactivate must write nothing."""
    period_start = datetime.now(UTC) - timedelta(days=5)
    period_end = datetime.now(UTC) + timedelta(days=26)
    sub_entry = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="subscription",
        source_payment_id=None,
        source_invoice_id="in_spend_read_fails",
        stripe_payment_id=None,
        amount_cents=1000,
        consumed_cents=0,
        purchased_at=period_start,
        effective_period_start=period_start,
        effective_period_end=period_end,
        expires_at=period_end,
        rolled_over_from_id=None,
        is_active=True,
    )
    db.add(sub_entry)
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=None,
            source_invoice_id=None,
            stripe_payment_id="pi_topup_spend_read_fails",
            amount_cents=500,
            consumed_cents=0,
            purchased_at=datetime.now(UTC) - timedelta(days=1),
            effective_period_start=None,
            effective_period_end=None,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            rolled_over_from_id=None,
            is_active=True,
        )
    )
    db.add(
        DBPrivateAIKey(
            name="spend-read-fails-key",
            litellm_token="spend-read-fails-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    db.commit()

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(side_effect=Exception("boom"))
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_spend_read_fails",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 502
    mock_litellm.update_team_budget.assert_not_awaited()
    mock_litellm.set_key_restrictions.assert_not_awaited()
    mock_record_payment.assert_not_awaited()
    assert (
        db.query(DBAuditLog)
        .filter(
            DBAuditLog.event_type == "subscription.deactivate",
            DBAuditLog.details["outcome"].as_string() == "spend_read_failed",
        )
        .count()
        == 1
    )
    db.refresh(sub_entry)
    assert sub_entry.is_active is True


@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_captures_snapshot_before_reset(
    mock_litellm_class,
    mock_record_payment,
    mock_capture_snapshot,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    period_start = datetime.now(UTC) - timedelta(days=5)
    period_end = datetime.now(UTC) + timedelta(days=26)
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            source_payment_id=None,
            source_invoice_id="in_active_sub_1",
            stripe_payment_id=None,
            amount_cents=1000,
            consumed_cents=250,
            purchased_at=period_start,
            effective_period_start=period_start,
            effective_period_end=period_end,
            expires_at=period_end,
            rolled_over_from_id=None,
            is_active=True,
        )
    )
    db.commit()

    mock_record_payment.return_value = 777
    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_capture_snapshot",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    mock_capture_snapshot.assert_awaited_once()
    assert mock_capture_snapshot.await_args.kwargs["team"].id == test_team.id
    assert mock_capture_snapshot.await_args.kwargs["region"].id == test_region.id
    assert mock_capture_snapshot.await_args.kwargs["period_start"] == period_start
    assert mock_capture_snapshot.await_args.kwargs["period_end"] == period_end
    assert (
        mock_capture_snapshot.await_args.kwargs["source_event_id"]
        == "txn_deactivate_capture_snapshot"
    )


@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_fifo_debits_topup_on_cancellation(
    mock_litellm_class,
    mock_record_payment,
    mock_capture_spend,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    """
    Regression test for: Subscription cancellation ignores in-period spend
    when computing top-up budget.

    Scenario (mirrors the ticket example):
      - Subscription budget: $1.00  (100¢)
      - Active top-up:       $1.68  (168¢, consumed_cents=0)
      - LiteLLM spend:       $2.10  (210¢) — overflows subscription into top-up
      - Previous period baseline: $0 (first period)

    Expected after cancellation:
      - FIFO allocates 210¢ of spend: 100¢ consumed by subscription entry
        (already inactive at cancel time) then 110¢ consumed by the top-up entry
        → top-up consumed_cents becomes 110, remaining = 168 - 110 = 58¢ ($0.58)
      - LiteLLM max_budget = current_spend ($2.10) + topup_remaining ($0.58) = $2.68
      - Key budget_amount = $0.58
    """
    period_start = datetime.now(UTC) - timedelta(days=5)
    period_end = datetime.now(UTC) + timedelta(days=26)

    # Active subscription ledger entry: $1.00
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            source_payment_id=None,
            source_invoice_id="in_fifo_test_sub",
            stripe_payment_id=None,
            amount_cents=100,
            consumed_cents=0,
            purchased_at=period_start,
            effective_period_start=period_start,
            effective_period_end=period_end,
            expires_at=period_end,
            rolled_over_from_id=None,
            is_active=True,
        )
    )
    # Active top-up entry: $1.68, not yet debited
    topup_entry = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="topup",
        source_payment_id=None,
        source_invoice_id=None,
        stripe_payment_id="pi_fifo_topup",
        amount_cents=168,
        consumed_cents=0,
        purchased_at=period_start,
        effective_period_start=None,
        effective_period_end=None,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        rolled_over_from_id=None,
        is_active=True,
    )
    db.add(topup_entry)
    db.add(
        DBPrivateAIKey(
            name="fifo-test-key",
            litellm_token="fifo-test-token",
            region_id=test_region.id,
            team_id=test_team.id,
        )
    )
    db.commit()

    mock_record_payment.return_value = 999
    mock_litellm = mock_litellm_class.return_value
    # LiteLLM reports $2.10 total spend (overflows $1.00 subscription into top-up)
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 2.10, "max_budget": 5.0}}
    )
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_fifo_cancel_test",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200

    # capture_periodic_team_spend_for_period must have been called before FIFO ran.
    mock_capture_spend.assert_awaited_once()
    assert mock_capture_spend.await_args.kwargs["team"].id == test_team.id
    assert mock_capture_spend.await_args.kwargs["region"].id == test_region.id
    assert mock_capture_spend.await_args.kwargs["period_start"] == period_start
    assert mock_capture_spend.await_args.kwargs["period_end"] == period_end
    assert (
        mock_capture_spend.await_args.kwargs["source_event_id"]
        == "txn_fifo_cancel_test"
    )

    # FIFO must have debited the top-up: 210¢ spend - 0¢ baseline = 210¢ incremental.
    # Subscription entry absorbs 100¢ (then deactivated), top-up absorbs remaining 110¢.
    db.refresh(topup_entry)
    assert topup_entry.consumed_cents == 110, (
        f"Top-up consumed_cents should be 110 after FIFO, got {topup_entry.consumed_cents}. "
        "Cancellation is not debiting mid-period spend against top-up credits."
    )

    # Remaining top-up: 168 - 110 = 58¢ = $0.58
    topup_remaining = 0.58
    current_spend = 2.10
    expected_max_budget = round(current_spend + topup_remaining, 2)

    mock_litellm.update_team_budget.assert_awaited_once()
    assert mock_litellm.get_team_info.await_count == 1
    assert "spend" not in mock_litellm.update_team_budget.await_args.kwargs
    assert (
        mock_litellm.update_team_budget.await_args.kwargs["clear_budget_duration"]
        is True
    )
    actual_max_budget = mock_litellm.update_team_budget.await_args.kwargs["max_budget"]
    assert abs(actual_max_budget - expected_max_budget) < 0.01, (
        f"Expected max_budget ~{expected_max_budget}, got {actual_max_budget}. "
        "Top-up remaining is not being correctly reduced by mid-period spend."
    )

    mock_litellm.set_key_restrictions.assert_awaited_once()
    actual_key_budget = mock_litellm.set_key_restrictions.await_args.kwargs[
        "budget_amount"
    ]
    assert abs(actual_key_budget - topup_remaining) < 0.01, (
        f"Expected key budget_amount ~{topup_remaining}, got {actual_key_budget}."
    )
    assert mock_litellm.set_key_restrictions.await_args.kwargs["duration"] is None


def _seed_deactivate_period(db, team, region, *, baseline_spend):
    """Active subscription period plus an older snapshot as the baseline."""
    period_start = datetime.now(UTC) - timedelta(days=5)
    period_end = datetime.now(UTC) + timedelta(days=26)
    sub_entry = DBPeriodicBudgetLedgerEntry(
        team_id=team.id,
        region_id=region.id,
        entry_type="subscription",
        source_invoice_id="in_cancel_logs_sub",
        amount_cents=10000,
        consumed_cents=0,
        purchased_at=period_start,
        effective_period_start=period_start,
        effective_period_end=period_end,
        expires_at=period_end,
        is_active=True,
    )
    db.add(sub_entry)
    db.add(
        DBTeamSpendPeriod(
            team_id=team.id,
            region_id=region.id,
            budget_type=team.budget_type,
            period_start=period_start - timedelta(days=31),
            period_end=period_start,
            total_spend=baseline_spend,
            source="test",
        )
    )
    db.commit()
    return sub_entry, period_start


@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_uses_spend_logs_when_counter_dropped(
    mock_litellm_class,
    mock_record_payment,
    _mock_capture_spend,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    """A counter below the stored baseline settles from the spend logs."""
    sub_entry, period_start = _seed_deactivate_period(
        db, test_team, test_region, baseline_spend=50.0
    )
    mock_record_payment.return_value = 991

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 2.0, "max_budget": 100.0}}
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(return_value=40.0)
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cancel_logs_drop",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    db.refresh(sub_entry)
    assert sub_entry.consumed_cents == 4000
    mock_litellm.get_team_spend_in_range.assert_awaited_once()
    args = mock_litellm.get_team_spend_in_range.await_args.args
    assert args[1] == period_start


@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.LiteLLMService")
def test_subscription_deactivate_uses_spend_logs_when_litellm_still_has_a_cycle(
    mock_litellm_class,
    mock_record_payment,
    _mock_capture_spend,
    client,
    admin_token,
    db,
    test_team,
    test_region,
):
    """A team that still carries a LiteLLM cycle settles from the spend logs,
    even when the counter reads above the baseline."""
    sub_entry, period_start = _seed_deactivate_period(
        db, test_team, test_region, baseline_spend=50.0
    )
    mock_record_payment.return_value = 992

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={
            "team_info": {
                "spend": 60.0,
                "max_budget": 100.0,
                "budget_duration": "31d",
            }
        }
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(return_value=40.0)
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_cancel_logs_cycle",
            "team_id": test_team.id,
            "region_id": test_region.id,
            "reason": "cancelled",
        },
    )

    assert response.status_code == 200
    db.refresh(sub_entry)
    # Live minus baseline would be 1000 cents; the logs win.
    assert sub_entry.consumed_cents == 4000
    mock_litellm.get_team_spend_in_range.assert_awaited_once()
    assert mock_litellm.get_team_spend_in_range.await_args.args[1] == period_start


def test_subscription_deactivate_endpoint_idempotent(
    client, admin_token, db, test_team
):
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="txn_deactivate_done",
        amount_cents=0,
        currency="usd",
        payment_type="deactivation",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    response = client.post(
        "/billing/subscription/deactivate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_deactivate_done",
            "team_id": test_team.id,
            "region_id": 999,
        },
    )

    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert response.json()["payment_id"] == payment.id


# ─── POOL team subscription cycle tests ──────────────────────────────────────


def _make_pool_team(db, name="Pool Sub Team"):
    """Create a POOL team with a stripe_customer_id."""
    team = DBTeam(
        name=name,
        admin_email=f"{name.lower().replace(' ', '_')}@example.com",
        is_active=True,
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
        stripe_customer_id=f"cus_pool_{name.replace(' ', '_').lower()}",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@patch("app.api.subscription._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.api.subscription.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.api.subscription._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch(
    "app.api.subscription.capture_periodic_team_spend_for_period",
    new_callable=AsyncMock,
)
def test_pool_subscription_cycle_endpoint_accepted(
    mock_capture,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    client,
    admin_token,
    db,
    test_region,
):
    """The /cycle endpoint must accept POOL teams (previously rejected with 400)."""
    pool_team = _make_pool_team(db, "Pool Cycle Accept")
    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 500

    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_pool_cycle_1",
            "budget_cents": 3000,
            "team_id": pool_team.id,
            "region_id": test_region.id,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["team_id"] == pool_team.id
    assert data["budget_dollars"] == 30.0
    mock_apply_cycle.assert_awaited_once()


def test_pool_subscription_cycle_endpoint_returns_404_for_unknown_team(
    client, admin_token, db, test_region
):
    """The /cycle endpoint returns 404 when the requested team does not exist."""
    response = client.post(
        "/billing/subscription/cycle",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "transaction_id": "txn_bad_team",
            "budget_cents": 1000,
            "team_id": 999999,
            "region_id": test_region.id,
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_pool_team_drift_reconciliation_returns_result(
    mock_litellm_class,
    db,
    test_region,
):
    """reconcile_periodic_team_budget_drift must return a BudgetDriftResult for
    POOL teams (previously returned None)."""
    pool_team = _make_pool_team(db, "Pool Drift Team")

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 10.0, "max_budget": 30.0}}
    )

    region = db.query(DBRegion).filter(DBRegion.id == test_region.id).first()
    result = await reconcile_periodic_team_budget_drift(
        db=db, team=pool_team, region=region
    )

    assert result is not None
    # max_budget=30, spend=10, no active ledger entries → expected = 10 + 0 + 0 = 10
    # drift = actual(30) - expected(10) = 20 dollars = 2000 cents
    assert result.actual_max_budget_cents == 3000
    assert result.expected_max_budget_cents == 1000
    assert result.drift_cents == 2000


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_region_litellm_keys")
@patch("app.core.worker.LimitService")
async def test_pool_team_billing_cycle_clears_duration_and_resets_spend(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_region,
):
    """apply_billing_cycle_for_team must work for POOL teams: no LiteLLM budget
    cycle on the team or its keys, key spend reset to 0.0, like PERIODIC."""
    pool_team = _make_pool_team(db, "Pool Cycle Direct")

    key = DBPrivateAIKey(
        name="pool-cycle-key",
        litellm_token="pool-cycle-token",
        region_id=test_region.id,
        team_id=pool_team.id,
    )
    db.add(key)
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        30.0,
        500,
    )
    mock_get_keys.return_value = [key]

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 5.0, "max_budget": 30.0}}
    )

    now = datetime.now(UTC)
    errors = await apply_billing_cycle_for_team(
        db=db,
        team_id=pool_team.id,
        budget_cents=3000,
        region_id=test_region.id,
        period_start=now,
        period_end=now + timedelta(days=31),
    )

    assert errors == []
    team_call = mock_litellm.update_team_budget.await_args
    assert "budget_duration" not in team_call.kwargs
    assert team_call.kwargs["clear_budget_duration"] is True
    # Team spend is non-resettable in LiteLLM, so projected max_budget is:
    # current_spend + current_cycle_remaining = 5.0 + 30.0
    assert team_call.kwargs["max_budget"] == 35.0

    key_call = mock_litellm.set_key_restrictions.await_args
    assert key_call.kwargs["spend"] == 0.0
    assert key_call.kwargs["duration"] == "31d"
    assert key_call.kwargs["budget_duration"] is None


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_sync_periodic_ledger_uses_spend_logs_when_litellm_counter_drops(
    mock_litellm_class,
    db,
    test_team,
    test_region,
    caplog,
):
    from app.core.periodic_budget_ledger_service import add_subscription_entry
    from app.core.worker import _sync_periodic_ledger_for_period
    from app.db.models import DBTeamSpendPeriod

    now = datetime.now(UTC)
    previous_start = now - timedelta(days=30)
    # Two earlier snapshots, as a team has from its third cycle on: the newest
    # one is the window start.
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type=test_team.budget_type,
            period_start=now - timedelta(days=60),
            period_end=previous_start,
            total_spend=20.0,
            source="test",
        )
    )
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type=test_team.budget_type,
            period_start=previous_start,
            period_end=now,
            total_spend=50.0,
            source="test",
        )
    )
    add_subscription_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=10000,
        purchased_at=previous_start,
        period_start=previous_start,
        period_end=now,
        source_payment_id=None,
        source_invoice_id="inv_prev",
    )
    db.commit()

    mock_litellm_class.format_team_id.return_value = "test_region_team"
    mock_litellm = mock_litellm_class.return_value
    # The team counter was reset by LiteLLM, so it now reads far below the
    # stored snapshot of 50.0.
    mock_litellm.get_team_info = AsyncMock(
        return_value={"team_info": {"spend": 2.0}, "keys": []}
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(return_value=40.0)

    with caplog.at_level("WARNING"):
        await _sync_periodic_ledger_for_period(
            db=db,
            team=test_team,
            region=test_region,
            period_start=now,
            period_end=now + timedelta(days=31),
            amount_cents=10000,
            source_payment_id=None,
            source_invoice_id="inv_new",
        )

    previous_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.source_invoice_id == "inv_prev")
        .first()
    )
    assert previous_entry.consumed_cents == 4000
    mock_litellm.get_team_spend_in_range.assert_awaited_once()
    args = mock_litellm.get_team_spend_in_range.await_args.args
    assert args[0] == "test_region_team"
    assert args[1] == previous_start
    assert args[2] == now
    assert "counter is not trustworthy" in caplog.text


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_sync_periodic_ledger_uses_spend_logs_when_litellm_still_has_a_cycle(
    mock_litellm_class,
    db,
    test_team,
    test_region,
):
    """A team that still carries a LiteLLM budget_duration can have been reset
    and climbed back above the snapshot, so the counter is not trusted."""
    from app.core.periodic_budget_ledger_service import add_subscription_entry
    from app.core.worker import _sync_periodic_ledger_for_period
    from app.db.models import DBTeamSpendPeriod

    now = datetime.now(UTC)
    previous_start = now - timedelta(days=30)
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type=test_team.budget_type,
            period_start=previous_start,
            period_end=now,
            total_spend=50.0,
            source="test",
        )
    )
    add_subscription_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=10000,
        purchased_at=previous_start,
        period_start=previous_start,
        period_end=now,
        source_payment_id=None,
        source_invoice_id="inv_prev",
    )
    db.commit()

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={
            "team_info": {"spend": 60.0, "budget_duration": "31d"},
            "keys": [],
        }
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(return_value=40.0)

    await _sync_periodic_ledger_for_period(
        db=db,
        team=test_team,
        region=test_region,
        period_start=now,
        period_end=now + timedelta(days=31),
        amount_cents=10000,
        source_payment_id=None,
        source_invoice_id="inv_new",
    )

    previous_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.source_invoice_id == "inv_prev")
        .first()
    )
    # The spend logs win over live minus baseline, which would be 1000 cents.
    assert previous_entry.consumed_cents == 4000
    mock_litellm.get_team_spend_in_range.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_sync_periodic_ledger_debits_counter_delta_without_a_litellm_cycle(
    mock_litellm_class,
    db,
    test_team,
    test_region,
):
    """With no LiteLLM cycle and a counter above the snapshot, the plain
    difference is debited and the spend logs are not read."""
    from app.core.periodic_budget_ledger_service import add_subscription_entry
    from app.core.worker import _sync_periodic_ledger_for_period
    from app.db.models import DBTeamSpendPeriod

    now = datetime.now(UTC)
    previous_start = now - timedelta(days=30)
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type=test_team.budget_type,
            period_start=previous_start,
            period_end=now,
            total_spend=50.0,
            source="test",
        )
    )
    add_subscription_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=10000,
        purchased_at=previous_start,
        period_start=previous_start,
        period_end=now,
        source_payment_id=None,
        source_invoice_id="inv_prev",
    )
    db.commit()

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={
            "team_info": {"spend": 60.0, "budget_duration": None},
            "keys": [],
        }
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(return_value=40.0)

    await _sync_periodic_ledger_for_period(
        db=db,
        team=test_team,
        region=test_region,
        period_start=now,
        period_end=now + timedelta(days=31),
        amount_cents=10000,
        source_payment_id=None,
        source_invoice_id="inv_new",
    )

    previous_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.source_invoice_id == "inv_prev")
        .first()
    )
    assert previous_entry.consumed_cents == 1000
    mock_litellm.get_team_spend_in_range.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_sync_periodic_ledger_raises_when_spend_logs_fail(
    mock_litellm_class,
    db,
    test_team,
    test_region,
):
    from app.core.periodic_budget_ledger_service import add_subscription_entry
    from app.core.worker import _sync_periodic_ledger_for_period
    from app.db.models import DBTeamSpendPeriod

    now = datetime.now(UTC)
    previous_start = now - timedelta(days=30)
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type=test_team.budget_type,
            period_start=previous_start,
            period_end=now,
            total_spend=50.0,
            source="test",
        )
    )
    add_subscription_entry(
        db,
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=10000,
        purchased_at=previous_start,
        period_start=previous_start,
        period_end=now,
        source_payment_id=None,
        source_invoice_id="inv_prev",
    )
    db.commit()

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.get_team_info = AsyncMock(
        return_value={
            "team_info": {"spend": 2.0},
            "keys": [{"spend": 1.5}, {"spend": 0.5}],
        }
    )
    mock_litellm.get_team_spend_in_range = AsyncMock(side_effect=Exception("boom"))

    with pytest.raises(Exception, match="boom"):
        await _sync_periodic_ledger_for_period(
            db=db,
            team=test_team,
            region=test_region,
            period_start=now,
            period_end=now + timedelta(days=31),
            amount_cents=10000,
            source_payment_id=None,
            source_invoice_id="inv_new",
        )

    # Nothing settled: a retry must find the ledger untouched.
    previous_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.source_invoice_id == "inv_prev")
        .first()
    )
    assert previous_entry.consumed_cents == 0
    assert (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.source_invoice_id == "inv_new")
        .first()
        is None
    )
