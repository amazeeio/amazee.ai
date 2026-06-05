from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.worker import (
    _record_periodic_payment_direct,
    _run_cycle_from_stripe_event,
    apply_billing_cycle_for_team,
    reconcile_periodic_team_budget_drift,
)
from app.db.models import (
    DBPeriodicBudgetLedgerEntry,
    DBPeriodicPayment,
    DBPrivateAIKey,
    DBRegion,
    DBTeam,
)
from app.schemas.models import BudgetType


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
    assert payment.sync_status == "success"


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
    assert mock_litellm.update_team_budget.await_args.kwargs["spend"] == 0.0
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


@pytest.mark.asyncio
@patch("app.core.worker.capture_periodic_team_spend_for_period", new_callable=AsyncMock)
@patch("app.core.worker._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.core.worker._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch("app.core.worker.apply_billing_cycle_for_team", new_callable=AsyncMock)
async def test_webhook_cycle_first_cycle_is_region_scoped(
    mock_apply_cycle,
    mock_sync_ledger,
    mock_record_payment,
    mock_capture,
    db,
    test_team,
    test_region,
):
    other_region = DBRegion(
        name="test-region-third",
        label="Test Region Third",
        description="Third region for webhook region-scoped cycle tests",
        postgres_host="amazee-test-postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://test-litellm-third.com",
        litellm_api_key="test-litellm-key-third",
        is_active=True,
    )
    db.add(other_region)
    db.commit()

    test_team.stripe_customer_id = "cus_cycle_scope"
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

    mock_record_payment.return_value = 987
    mock_apply_cycle.return_value = []

    event_object = SimpleNamespace(
        subscription="sub_region_scope",
        amount_paid=10000,
        id="in_region_scope",
        currency="usd",
        parent=SimpleNamespace(
            subscription_details=SimpleNamespace(
                subscription="sub_region_scope",
                metadata={"regionId": str(test_region.id)},
            )
        ),
    )

    await _run_cycle_from_stripe_event(
        db=db,
        event_id="evt_region_scope",
        customer_id="cus_cycle_scope",
        event_object=event_object,
    )

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
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 0.0
    assert (
        mock_litellm.update_team_budget.await_args.kwargs["budget_duration"] == "365d"
    )
    assert mock_litellm.update_team_budget.await_args.kwargs["spend"] == 0.0
    mock_litellm.set_key_restrictions.assert_awaited_once()
    assert mock_litellm.set_key_restrictions.await_args.kwargs["budget_amount"] == 0.0
    assert (
        mock_litellm.set_key_restrictions.await_args.kwargs["budget_duration"] == "365d"
    )
    assert mock_litellm.set_key_restrictions.await_args.kwargs["spend"] == 0.0


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
    assert mock_litellm.update_team_budget.await_args.kwargs["max_budget"] == 4.0
    assert (
        mock_litellm.update_team_budget.await_args.kwargs["budget_duration"] == "365d"
    )
    assert mock_litellm.update_team_budget.await_args.kwargs["spend"] == 0.0
    mock_litellm.set_key_restrictions.assert_awaited_once()
    assert mock_litellm.set_key_restrictions.await_args.kwargs["budget_amount"] == 4.0
    assert (
        mock_litellm.set_key_restrictions.await_args.kwargs["budget_duration"] == "365d"
    )
    assert mock_litellm.set_key_restrictions.await_args.kwargs["spend"] == 0.0


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
@patch("app.core.worker._record_periodic_payment_direct", new_callable=AsyncMock)
@patch("app.core.worker.apply_billing_cycle_for_team", new_callable=AsyncMock)
@patch("app.core.worker._sync_periodic_ledger_for_period", new_callable=AsyncMock)
@patch("app.core.worker.capture_periodic_team_spend_for_period", new_callable=AsyncMock)
async def test_pool_team_invoice_paid_not_skipped(
    mock_capture_period,
    mock_sync_ledger,
    mock_apply_cycle,
    mock_record_payment,
    db,
    test_region,
):
    """POOL teams must NOT be skipped on invoice.paid — _run_cycle_from_stripe_event
    must proceed through the billing cycle for POOL teams."""
    pool_team = _make_pool_team(db, "Pool Invoice Team")
    mock_apply_cycle.return_value = []
    mock_record_payment.return_value = 501

    event_object = SimpleNamespace(
        id="inv_pool_1",
        subscription="sub_pool_1",
        amount_paid=3000,
        period_start=1700000000,
        period_end=1702678400,
        parent=SimpleNamespace(
            subscription_details=SimpleNamespace(
                subscription="sub_pool_1",
                metadata={"regionId": str(test_region.id)},
            )
        ),
    )

    await _run_cycle_from_stripe_event(
        db=db,
        event_id="evt_pool_invoice",
        customer_id=pool_team.stripe_customer_id,
        event_object=event_object,
    )

    mock_apply_cycle.assert_awaited_once()


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
async def test_pool_team_billing_cycle_uses_31d_and_resets_spend(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db,
    test_region,
):
    """apply_billing_cycle_for_team must work for POOL teams, using 31d duration
    and resetting key spend to 0.0, exactly like PERIODIC teams."""
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
    assert team_call.kwargs["budget_duration"] == "31d"
    assert team_call.kwargs["max_budget"] == 30.0

    key_call = mock_litellm.set_key_restrictions.await_args
    assert key_call.kwargs["spend"] == 0.0
    assert key_call.kwargs["budget_duration"] == "31d"
