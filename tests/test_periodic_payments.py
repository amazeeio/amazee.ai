import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy.orm import Session
from app.db.models import DBPeriodicPayment
from app.core.worker import _record_periodic_payment, apply_product_for_team


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
    event = MagicMock()
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
