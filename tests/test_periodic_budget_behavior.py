"""
Tests for PERIODIC team budget behaviour introduced by the
"Spend API: align duration budget for PERIODIC teams with Stripe webhooks" PR,
extended to cover POOL subscription teams (AI-398).

Key behaviours under test:
1. PERIODIC teams use a fixed 31-day budget duration (not days_left_in_period)
2. POOL subscription teams use 31d duration (same as PERIODIC, via billing cycle)
3. PERIODIC teams compound max_budget = accumulated_spend + monthly_cap
4. Compounding falls back to flat cap when get_team_info fails
5. PERIODIC teams reset key spend to 0 on each webhook
6. POOL subscription teams reset key spend to 0 on cycle (same as PERIODIC)
7. Spend API: PERIODIC teams derive total_spend from sum of key spends
8. Spend API: PERIODIC teams derive total_budget from current-cycle ledger budget
"""

import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, AsyncMock

from app.db.models import (
    DBPrivateAIKey,
    DBTeam,
    DBPoolPurchase,
    DBPeriodicPayment,
)
from app.schemas.models import BudgetType
from app.core.worker import apply_billing_cycle_for_team, apply_product_for_team


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _apply_periodic_cycle(
    db, team, region, *, budget_cents=10000, payment_id=None
):
    now = datetime.now(UTC)
    return await apply_billing_cycle_for_team(
        db=db,
        team_id=team.id,
        budget_cents=budget_cents,
        region_id=region.id,
        period_start=now,
        period_end=now + timedelta(days=31),
        source_payment_id=payment_id,
    )


def _make_pool_team(db, name="Pool Team", with_purchase=True, region=None):
    """Create a POOL team (requires_pool_purchase_gate=True)."""
    team = DBTeam(
        name=name,
        admin_email=f"{name.lower().replace(' ', '')}@example.com",
        is_active=True,
        budget_type=BudgetType.POOL,
        require_purchase_for_requests=True,
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    if with_purchase and region:
        db.add(
            DBPoolPurchase(
                team_id=team.id,
                region_id=region.id,
                amount_cents=10000,
                currency="USD",
                purchased_at=datetime.now(UTC),
                stripe_payment_id=f"pool-{team.id}-{region.id}",
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    return team


def _make_keys(db, team, region, count=2):
    """Create team-owned keys for the given team+region."""
    keys = []
    for i in range(count):
        key = DBPrivateAIKey(
            name=f"key-{team.id}-{i}",
            litellm_token=f"token-{team.id}-{i}",
            region_id=region.id,
            team_id=team.id,
        )
        db.add(key)
        keys.append(key)
    db.commit()
    return keys


# ─── 1. PERIODIC teams use 31d duration ───────────────────────────────────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_team_uses_31d_duration(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """PERIODIC teams must always use a fixed 31-day budget duration,
    regardless of days_left_in_period from the limit service."""
    test_team.stripe_customer_id = "cus_periodic_31d"
    db.commit()

    # Limit service returns 15 days left — PERIODIC must still use 31d
    mock_limit_service.return_value.get_token_restrictions.return_value = (
        15,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: []}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await _apply_periodic_cycle(db, test_team, test_region)

    # Team-level budget must use 31d
    team_call = mock_litellm.update_team_budget.await_args
    assert team_call.kwargs["budget_duration"] == "31d"


# ─── 2. POOL subscription teams use 31d duration (same as PERIODIC) ──────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_pool_subscription_team_uses_31d_duration(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_product,
    test_region,
):
    """POOL subscription teams go through apply_billing_cycle_for_team and
    therefore use the same fixed 31d budget_duration as PERIODIC teams."""
    team = _make_pool_team(db, "Pool Duration Team", region=test_region)
    team.stripe_customer_id = "cus_pool_duration"
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        20,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: []}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await apply_product_for_team(
        db, "cus_pool_duration", test_product.id, datetime.now(UTC)
    )

    team_call = mock_litellm.update_team_budget.await_args
    assert team_call.kwargs["budget_duration"] == "31d"


# ─── 3. PERIODIC teams compound max_budget ────────────────────────────────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_team_compounds_max_budget(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """PERIODIC webhook resets key spend to 0; max_budget = desired_remaining = monthly_cap + topup."""
    test_team.stripe_customer_id = "cus_compound"
    db.commit()

    keys = _make_keys(db, test_team, test_region, count=1)

    # monthly cap = 100.0, 31 days, 1000 rpm
    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: keys}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()
    # Team has already spent 37.50 in prior periods
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 37.50}})

    await _apply_periodic_cycle(db, test_team, test_region)

    # Webhook resets key spend to 0; max_budget = desired_remaining = cap + topup = 100.0 + 0.0
    # (get_team_info is called to detect region availability but spend is not used in the formula)
    team_call = mock_litellm.update_team_budget.await_args
    assert team_call.kwargs["max_budget"] == 100.0


# ─── 4. Compounding falls back to flat cap when get_team_info fails ───────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_compounding_stops_on_get_team_info_failure(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """When get_team_info fails, apply_product_for_team must abort the sync
    (break out of the region loop), NOT continue with a flat cap.

    The payment record must be left as sync_failed so a future retry
    process can pick it up. No update_team_budget or set_key_restrictions
    calls should be made after the failure.

    Rationale: get_team_info is the first LiteLLM call in the sync process.
    If we can't read the current team spend, compounding is impossible.
    Continuing with a flat cap would shortchange the team by
    accumulated_spend, and the payment would be incorrectly marked as
    "success" with inaccurate data.
    """
    test_team.stripe_customer_id = "cus_fallback"
    db.commit()

    keys = _make_keys(db, test_team, test_region, count=1)

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: keys}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()
    # get_team_info blows up
    mock_litellm.get_team_info = AsyncMock(side_effect=Exception("LiteLLM down"))

    # Create a payment record to track sync status
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_fallback",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    await _apply_periodic_cycle(db, test_team, test_region, payment_id=payment.id)

    # Must NOT have called update_team_budget or set_key_restrictions
    mock_litellm.update_team_budget.assert_not_awaited()
    mock_litellm.set_key_restrictions.assert_not_awaited()

    # Payment record must be sync_failed with the error logged
    db.refresh(payment)
    assert payment.sync_status == "sync_failed"
    assert "LiteLLM down" in payment.error_log


# ─── 5. PERIODIC teams reset key spend to 0 ──────────────────────────────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_team_resets_key_spend_to_zero(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """PERIODIC teams must pass spend=0.0 to set_key_restrictions so that
    each key's spend counter is reset at the start of a new billing period."""
    test_team.stripe_customer_id = "cus_spend_reset"
    db.commit()

    keys = _make_keys(db, test_team, test_region, count=2)

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        50.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: keys}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await _apply_periodic_cycle(db, test_team, test_region, budget_cents=5000)

    # Every key must have been called with spend=0.0
    assert mock_litellm.set_key_restrictions.call_count == 2
    for call in mock_litellm.set_key_restrictions.call_args_list:
        assert call.kwargs["spend"] == 0.0


# ─── 6. POOL subscription teams reset key spend to 0 on cycle ─────────────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_pool_subscription_team_resets_key_spend(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_product,
    test_region,
):
    """POOL subscription teams go through apply_billing_cycle_for_team and
    therefore reset key spend to 0.0 on each cycle, just like PERIODIC teams."""
    team = _make_pool_team(db, "Pool No Reset Team", region=test_region)
    team.stripe_customer_id = "cus_pool_no_reset"
    db.commit()

    keys = _make_keys(db, team, test_region, count=2)

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        20,
        50.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: keys}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await apply_product_for_team(
        db, "cus_pool_no_reset", test_product.id, datetime.now(UTC)
    )

    # Every key must have been called with spend=0.0 (reset on cycle)
    assert mock_litellm.set_key_restrictions.call_count == 2
    for call in mock_litellm.set_key_restrictions.call_args_list:
        assert call.kwargs["spend"] == 0.0


# ─── 7. Spend API: PERIODIC team total_spend = sum of key spends ──────────


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_periodic_team_total_spend_is_sum_of_key_spends(
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    """For PERIODIC teams, total_spend must be derived from sum of per-key
    spends (which are reset to 0 on each webhook), NOT from team_info.spend
    (which compounds and never resets)."""
    test_team.budget_type = "periodic"
    db.add(test_team)
    db.commit()

    key_a = DBPrivateAIKey(
        name="periodic-spend-a",
        litellm_token="periodic-spend-a-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    key_b = DBPrivateAIKey(
        name="periodic-spend-b",
        litellm_token="periodic-spend-b-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add_all([key_a, key_b])
    db.commit()

    # team_info.spend = 150 (compounded, includes old periods)
    # key spends: 12.50 + 7.50 = 20.00 (current period only)
    mock_get_team_info.return_value = {
        "team_info": {"spend": 150.0, "max_budget": 250.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_a.name},
                "user_id": None,
                "spend": 12.50,
                "max_budget": 100.0,
            },
            {
                "metadata": {"amazeeai_private_ai_key_name": key_b.name},
                "user_id": None,
                "spend": 7.50,
                "max_budget": 100.0,
            },
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    # total_spend must be 20.0 (sum of key spends), NOT 150.0 (compounded)
    assert data["total_spend"] == 20.0


# ─── 8. Spend API: POOL team uses team_info.spend (not sum of keys) ──────


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_pool_team_total_spend_uses_team_info(
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    """POOL teams must use team_info.spend as-is (no sum-of-keys logic)."""
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=10000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-spend-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    key_a = DBPrivateAIKey(
        name="pool-spend-a",
        litellm_token="pool-spend-a-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key_a)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"spend": 42.0, "max_budget": 100.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_a.name},
                "user_id": None,
                "spend": 42.0,
                "max_budget": 100.0,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    # POOL teams use team_info.spend directly
    assert data["total_spend"] == 42.0


# ─── 9. Spend API: PERIODIC team total_budget from ledger current-cycle ───


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_periodic_team_total_budget_from_current_cycle_ledger_budget(
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    """For PERIODIC teams, total_budget is derived from DB ledger current-cycle
    budget (subscription+topup remaining), not from LiteLLM key/team max_budget."""
    test_team.budget_type = "periodic"
    db.add(test_team)
    db.commit()

    key_a = DBPrivateAIKey(
        name="periodic-budget-a",
        litellm_token="periodic-budget-a-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    key_b = DBPrivateAIKey(
        name="periodic-budget-b",
        litellm_token="periodic-budget-b-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add_all([key_a, key_b])
    db.commit()

    # LiteLLM reports compounded values, but endpoint budget source for PERIODIC
    # teams is ledger current-cycle budget.
    mock_get_team_info.return_value = {
        "team_info": {"spend": 150.0, "max_budget": 250.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_a.name},
                "user_id": None,
                "spend": 12.50,
                "max_budget": 100.0,
            },
            {
                "metadata": {"amazeeai_private_ai_key_name": key_b.name},
                "user_id": None,
                "spend": 7.50,
                "max_budget": 100.0,
            },
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()

    # No active periodic ledger entries were created in this fixture, so
    # current-cycle purchased budget is 0.
    assert data["total_budget"] == 0.0


# ─── 10. PERIODIC team payment sync status updated on success ─────────────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_payment_sync_status_updated_on_success(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """When apply_product_for_team succeeds, the payment record's sync_status
    must be set to 'success'."""
    test_team.stripe_customer_id = "cus_sync_success"
    db.commit()

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: []}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    # Create a payment record
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_ok",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    await _apply_periodic_cycle(db, test_team, test_region, payment_id=payment.id)

    db.refresh(payment)
    assert payment.sync_status == "success"
    assert payment.error_log is None


# ─── 11. PERIODIC team payment sync status updated on partial failure ─────


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_periodic_payment_sync_status_updated_on_key_failure(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_team,
    test_product,
    test_region,
):
    """When some keys fail during apply_product_for_team, the payment record
    must be set to 'sync_failed' with error details."""
    test_team.stripe_customer_id = "cus_sync_partial_fail"
    db.commit()

    keys = _make_keys(db, test_team, test_region, count=2)

    mock_limit_service.return_value.get_token_restrictions.return_value = (
        31,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: keys}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    # First key succeeds, second fails
    mock_litellm.set_key_restrictions = AsyncMock(
        side_effect=[None, Exception("Key update timeout")]
    )

    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_sync_partial",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    await _apply_periodic_cycle(db, test_team, test_region, payment_id=payment.id)

    db.refresh(payment)
    assert payment.sync_status == "sync_failed"
    assert "Key update timeout" in payment.error_log
