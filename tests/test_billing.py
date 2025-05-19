import pytest
from unittest.mock import Mock, patch
from app.api.billing import handle_stripe_event_background

@pytest.mark.asyncio
async def test_handle_checkout_session_completed(db, test_team):
    # Arrange
    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_event.data.object = mock_session

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    db.refresh(test_team)
    assert test_team.stripe_customer_id == "cus_123"

@pytest.mark.asyncio
async def test_handle_subscription_deleted(db, test_team):
    # Arrange
    # First set up the team with a stripe customer ID
    test_team.stripe_customer_id = "cus_123"
    db.commit()
    db.refresh(test_team)

    mock_event = Mock()
    mock_event.type = "customer.subscription.deleted"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_event.data.object = mock_subscription

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    db.refresh(test_team)
    assert test_team.stripe_customer_id is None

@pytest.mark.asyncio
async def test_handle_checkout_session_completed_team_not_found(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": "999"}  # Non-existent team ID
    mock_event.data.object = mock_session

    # Act
    await handle_stripe_event_background(mock_event, db)

    # No assertion needed as we're just verifying no error occurs

@pytest.mark.asyncio
async def test_handle_subscription_deleted_team_not_found(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "customer.subscription.deleted"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_999"  # Non-existent customer ID
    mock_event.data.object = mock_subscription

    # Act
    await handle_stripe_event_background(mock_event, db)

    # No assertion needed as we're just verifying no error occurs

@pytest.mark.asyncio
async def test_handle_unknown_event_type(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "unknown.event.type"

    # Act
    await handle_stripe_event_background(mock_event, db)

    # No assertion needed as we're just verifying no error occurs