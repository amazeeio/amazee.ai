import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy.orm import Session
from app.db.models import DBPeriodicPayment
from app.core.worker import (
    _record_periodic_payment,
    apply_product_for_team,
    handle_stripe_event_background,
)


@pytest.fixture
def mock_event():
    event = MagicMock()
    event.id = "evt_test_123"
    event.customer = "cus_test_123"
    event.amount_paid = 1000
    event.currency = "usd"
    event.metadata = {"some": "data"}
    return event


@pytest.fixture
def mock_session_event():
    event = MagicMock(spec=["id", "customer", "amount_total", "currency", "metadata"])
    event.id = "cs_test_123"
    event.customer = "cus_test_123"
    event.amount_total = 5000
    event.currency = "usd"
    event.metadata = {"ai_budget_increase": "5000", "teamId": "1"}
    return event


@pytest.mark.asyncio
async def test_record_periodic_payment_subscription(db: Session, test_team, mock_event):
    # Setup team with matching stripe_customer_id
    test_team.stripe_customer_id = mock_event.customer
    db.commit()

    record_id = await _record_periodic_payment(db, mock_event)
    assert record_id is not None

    payment = (
        db.query(DBPeriodicPayment).filter(DBPeriodicPayment.id == record_id).first()
    )
    assert payment.stripe_payment_id == mock_event.id
    assert payment.amount_cents == 1000
    assert payment.payment_type == "subscription"
    assert payment.sync_status == "pending"


@pytest.mark.asyncio
async def test_record_periodic_payment_topup(
    db: Session, test_team, mock_session_event
):
    test_team.stripe_customer_id = mock_session_event.customer
    db.commit()

    record_id = await _record_periodic_payment(db, mock_session_event)
    assert record_id is not None

    payment = (
        db.query(DBPeriodicPayment).filter(DBPeriodicPayment.id == record_id).first()
    )
    assert payment.payment_type == "topup"
    assert payment.amount_cents == 5000


@pytest.mark.asyncio
async def test_record_periodic_payment_idempotency(db: Session, test_team, mock_event):
    test_team.stripe_customer_id = mock_event.customer
    db.commit()

    id1 = await _record_periodic_payment(db, mock_event)
    id2 = await _record_periodic_payment(db, mock_event)

    assert id1 == id2
    count = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == mock_event.id)
        .count()
    )
    assert count == 1


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_apply_product_updates_sync_status_success(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db: Session,
    test_team,
    test_product,
    test_region,
):
    # Setup
    test_team.stripe_customer_id = "cus_sync_ok"
    db.commit()

    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_ok",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    # Mock dependencies
    mock_limit_service.return_value.get_token_restrictions.return_value = (
        30,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: []}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock()
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await apply_product_for_team(
        db, "cus_sync_ok", test_product.id, datetime.now(UTC), payment.id
    )

    db.refresh(payment)
    assert payment.sync_status == "success"
    assert payment.error_log is None


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.get_team_keys_by_region")
@patch("app.core.worker.LimitService")
async def test_apply_product_updates_sync_status_failure(
    mock_limit_service,
    mock_get_keys,
    mock_litellm_class,
    db: Session,
    test_team,
    test_product,
    test_region,
):
    # Setup
    test_team.stripe_customer_id = "cus_sync_fail"
    db.commit()

    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="pay_fail",
        amount_cents=1000,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="pending",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.commit()

    # Mock dependencies
    mock_limit_service.return_value.get_token_restrictions.return_value = (
        30,
        100.0,
        1000,
    )
    mock_get_keys.return_value = {test_region: []}

    mock_litellm = mock_litellm_class.return_value
    mock_litellm.update_team_budget = AsyncMock(
        side_effect=Exception("Gateway Timeout")
    )
    mock_litellm.get_team_info = AsyncMock(return_value={"team_info": {"spend": 0.0}})

    await apply_product_for_team(
        db, "cus_sync_fail", test_product.id, datetime.now(UTC), payment.id
    )

    db.refresh(payment)
    assert payment.sync_status == "sync_failed"
    assert "Gateway Timeout" in payment.error_log


# --- Tests for AttributeError fallback paths in handle_stripe_event_background ---


@pytest.mark.asyncio
@patch("app.core.worker.get_db")
@patch("app.core.worker.apply_product_for_team", new_callable=AsyncMock)
@patch("app.core.worker.get_product_id_from_subscription", new_callable=AsyncMock)
@patch("app.core.worker._record_periodic_payment", new_callable=AsyncMock)
async def test_invoice_success_no_subscription_no_parent(
    mock_record,
    mock_get_product_id,
    mock_apply_product,
    mock_get_db,
):
    """Invoice success event with no subscription attribute and no parent
    should complete without error and NOT call apply_product_for_team."""
    mock_db = MagicMock()
    mock_db.close = MagicMock()
    mock_get_db.return_value = iter([mock_db])
    mock_record.return_value = None

    event = MagicMock()
    event.type = "invoice.paid"
    event_object = MagicMock()
    event_object.customer = "cus_inv_no_sub"
    event_object.subscription = None
    del event_object.parent
    event_object.period_start = int(datetime.now(UTC).timestamp())
    event.data.object = event_object

    await handle_stripe_event_background(event)

    mock_apply_product.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.core.worker.get_db")
@patch("app.core.worker.apply_product_for_team", new_callable=AsyncMock)
@patch("app.core.worker.get_product_id_from_subscription", new_callable=AsyncMock)
@patch("app.core.worker._record_periodic_payment", new_callable=AsyncMock)
async def test_invoice_success_parent_missing_subscription_details(
    mock_record,
    mock_get_product_id,
    mock_apply_product,
    mock_get_db,
):
    """Invoice success event where parent exists but subscription_details
    raises AttributeError should complete without error."""
    mock_db = MagicMock()
    mock_db.close = MagicMock()
    mock_get_db.return_value = iter([mock_db])
    mock_record.return_value = None

    event = MagicMock()
    event.type = "invoice.paid"
    event_object = MagicMock()
    event_object.customer = "cus_inv_attr_err"
    event_object.subscription = None
    event_object.parent = MagicMock()
    # Make subscription_details.subscription raise AttributeError
    del event_object.parent.subscription_details.subscription
    event_object.period_start = int(datetime.now(UTC).timestamp())
    event.data.object = event_object

    await handle_stripe_event_background(event)

    # Should not crash; apply_product should not be called since
    # subscription remains None after the AttributeError
    mock_apply_product.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.core.worker.get_db")
@patch("app.core.worker.apply_product_for_team", new_callable=AsyncMock)
@patch("app.core.worker.get_product_id_from_subscription", new_callable=AsyncMock)
@patch(
    "app.core.worker.capture_periodic_team_spend_for_invoice", new_callable=AsyncMock
)
@patch("app.core.worker._sync_periodic_ledger_for_invoice", new_callable=AsyncMock)
@patch("app.core.worker._record_periodic_payment", new_callable=AsyncMock)
async def test_invoice_success_passes_region_id_from_metadata(
    mock_record,
    mock_sync_ledger,
    mock_capture_spend,
    mock_get_product_id,
    mock_apply_product,
    mock_get_db,
):
    mock_db = MagicMock()
    mock_db.close = MagicMock()
    mock_get_db.return_value = iter([mock_db])
    mock_record.return_value = 77
    mock_get_product_id.return_value = "prod_test_123"

    event = MagicMock()
    event.type = "invoice.paid"
    event_object = MagicMock()
    event_object.customer = "cus_inv_region"
    event_object.subscription = "sub_inv_region"
    event_object.period_start = int(datetime.now(UTC).timestamp())
    event_object.metadata = {"regionId": "42"}
    event.data.object = event_object

    await handle_stripe_event_background(event)

    mock_apply_product.assert_awaited_once()
    kwargs = mock_apply_product.await_args.kwargs
    assert kwargs["region_id"] == 42


@pytest.mark.asyncio
@patch("app.core.worker.get_db")
@patch("app.core.worker.remove_product_from_team", new_callable=AsyncMock)
@patch("app.core.worker.get_product_id_from_subscription", new_callable=AsyncMock)
async def test_invoice_failure_no_subscription_no_parent(
    mock_get_product_id,
    mock_remove_product,
    mock_get_db,
):
    """Invoice failure event with no subscription and no parent
    should complete without error and NOT call remove_product_from_team."""
    mock_db = MagicMock()
    mock_db.close = MagicMock()
    mock_get_db.return_value = iter([mock_db])

    event = MagicMock()
    event.type = "invoice.payment_failed"
    event_object = MagicMock()
    event_object.customer = "cus_inv_fail_no_sub"
    event_object.subscription = None
    del event_object.parent
    event.data.object = event_object

    await handle_stripe_event_background(event)

    mock_remove_product.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.core.worker.get_db")
@patch("app.core.worker.remove_product_from_team", new_callable=AsyncMock)
@patch("app.core.worker.get_product_id_from_subscription", new_callable=AsyncMock)
async def test_invoice_failure_parent_missing_subscription_details(
    mock_get_product_id,
    mock_remove_product,
    mock_get_db,
):
    """Invoice failure event where parent exists but subscription_details
    raises AttributeError should complete without error."""
    mock_db = MagicMock()
    mock_db.close = MagicMock()
    mock_get_db.return_value = iter([mock_db])

    event = MagicMock()
    event.type = "invoice.payment_failed"
    event_object = MagicMock()
    event_object.customer = "cus_inv_fail_attr_err"
    event_object.subscription = None
    event_object.parent = MagicMock()
    del event_object.parent.subscription_details.subscription
    event.data.object = event_object

    await handle_stripe_event_background(event)

    mock_remove_product.assert_not_awaited()
