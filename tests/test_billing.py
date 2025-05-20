import pytest
from unittest.mock import Mock, patch, AsyncMock
from app.api.billing import handle_stripe_event_background

@pytest.mark.asyncio
async def test_handle_checkout_session_completed(db, test_team):
    # Arrange
    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    with patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock) as mock_get_product:
        mock_get_product.return_value = "prod_123"
        with patch('app.api.billing.apply_product_for_team', new_callable=AsyncMock) as mock_apply:
            # Act
            await handle_stripe_event_background(mock_event, db)

            # Assert
            mock_get_product.assert_called_once_with("cs_123")
            mock_apply.assert_called_once_with(db, "cus_123", "prod_123")

@pytest.mark.asyncio
async def test_handle_invoice_payment_succeeded(db, test_team):
    # Arrange
    mock_event = Mock()
    mock_event.type = "invoice.payment_succeeded"
    mock_invoice = Mock()
    mock_invoice.customer = "cus_123"
    mock_subscription = Mock()
    mock_subscription.id = "sub_123"
    mock_invoice.parent = Mock()
    mock_invoice.parent.subscription_details = Mock()
    mock_invoice.parent.subscription_details.subscription = "sub_123"
    mock_event.data.object = mock_invoice

    with patch('app.api.billing.get_product_id_from_sub', new_callable=AsyncMock) as mock_get_product:
        mock_get_product.return_value = "prod_123"
        with patch('app.api.billing.apply_product_for_team', new_callable=AsyncMock) as mock_apply:
            # Act
            await handle_stripe_event_background(mock_event, db)

            # Assert
            mock_get_product.assert_called_once_with("sub_123")
            mock_apply.assert_called_once_with(db, "cus_123", "prod_123")

@pytest.mark.asyncio
async def test_handle_subscription_deleted(db, test_team):
    # Arrange
    mock_event = Mock()
    mock_event.type = "customer.subscription.deleted"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_event.data.object = mock_subscription

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    # No assertions needed as we're just verifying no error occurs
    # The function now only logs the event

@pytest.mark.asyncio
async def test_handle_checkout_session_completed_team_not_found(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": "999"}  # Non-existent team ID
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    with patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock) as mock_get_product:
        mock_get_product.return_value = "prod_123"
        with patch('app.api.billing.apply_product_for_team', new_callable=AsyncMock) as mock_apply:
            # Act
            await handle_stripe_event_background(mock_event, db)

            # Assert
            mock_get_product.assert_called_once_with("cs_123")
            mock_apply.assert_called_once_with(db, "cus_123", "prod_123")

@pytest.mark.asyncio
async def test_handle_unknown_event_type(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "unknown.event.type"

    # Act
    await handle_stripe_event_background(mock_event, db)

    # No assertion needed as we're just verifying no error occurs