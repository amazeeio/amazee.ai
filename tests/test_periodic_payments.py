from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.worker import (
    _record_periodic_payment_direct,
    apply_billing_cycle_for_team,
)
from app.db.models import DBPeriodicBudgetLedgerEntry, DBPeriodicPayment, DBPrivateAIKey


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer changeme"}


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
    assert payment.sync_status == "pending"


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
    assert mock_litellm.update_team_budget.await_args.kwargs["budget_duration"] == "31d"
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 100.0
    mock_litellm.set_key_restrictions.assert_awaited_once()
    assert mock_litellm.set_key_restrictions.await_args.kwargs["budget_amount"] == 100.0
    assert mock_litellm.set_key_restrictions.await_args.kwargs["spend"] == 0.0
    assert mock_litellm.set_key_restrictions.await_args.kwargs["rpm_limit"] == 1000


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
    test_team,
    test_region,
):
    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 123

    response = client.post(
        "/billing/subscription/cycle",
        headers=_auth_headers(),
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
    mock_sync_ledger.assert_not_awaited()
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
        headers=_auth_headers(),
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


def test_subscription_cycle_endpoint_idempotent(client, db, test_team):
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
        headers=_auth_headers(),
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
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.set_key_restrictions = AsyncMock()

    response = client.post(
        "/billing/subscription/deactivate",
        headers=_auth_headers(),
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
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 0.0
    mock_litellm.set_key_restrictions.assert_awaited_once()
    assert mock_litellm.set_key_restrictions.await_args.kwargs["budget_amount"] == 0.0


def test_subscription_deactivate_endpoint_idempotent(client, db, test_team):
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
        headers=_auth_headers(),
        json={
            "transaction_id": "txn_deactivate_done",
            "team_id": test_team.id,
            "region_id": 999,
        },
    )

    assert response.status_code == 200
    assert response.json()["idempotent"] is True
    assert response.json()["payment_id"] == payment.id
