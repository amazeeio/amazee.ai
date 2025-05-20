import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi import HTTPException
from app.api.billing import handle_stripe_event_background, get_portal
from app.db.models import DBTeam

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

@patch('app.api.billing.create_portal_session', new_callable=AsyncMock)
def test_get_portal_existing_customer(mock_create_portal, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_portal_url = "https://billing.stripe.com/portal/123"
    mock_create_portal.return_value = mock_portal_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/portal",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_portal_url
    mock_create_portal.assert_called_once_with(
        "cus_123",
        f"http://localhost:3000/teams/{test_team.id}/dashboard"
    )

@patch('app.api.billing.create_portal_session', new_callable=AsyncMock)
@patch('app.api.billing.create_stripe_customer', new_callable=AsyncMock)
def test_get_portal_create_customer(mock_create_customer, mock_create_portal, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = None
    db.add(test_team)
    db.commit()

    mock_portal_url = "https://billing.stripe.com/portal/123"
    mock_create_customer.return_value = "cus_new_123"
    mock_create_portal.return_value = mock_portal_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/portal",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_portal_url
    mock_create_customer.assert_called_once()
    mock_create_portal.assert_called_once_with(
        "cus_new_123",
        f"http://localhost:3000/teams/{test_team.id}/dashboard"
    )

def test_get_portal_team_not_found(client, db, admin_token):
    # Arrange
    non_existent_team_id = 999

    # Act
    response = client.post(
        f"/billing/teams/{non_existent_team_id}/portal",
        headers={"Authorization": f"Bearer {admin_token}"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "Team not found"

@patch('app.api.billing.create_portal_session', new_callable=AsyncMock)
def test_get_portal_as_system_admin(mock_create_portal, client, db, test_team, admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_portal_url = "https://billing.stripe.com/portal/123"
    mock_create_portal.return_value = mock_portal_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/portal",
        headers={"Authorization": f"Bearer {admin_token}"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_portal_url
    mock_create_portal.assert_called_once_with(
        "cus_123",
        f"http://localhost:3000/teams/{test_team.id}/dashboard"
    )

@patch('app.api.billing.create_portal_session', new_callable=AsyncMock)
def test_get_portal_stripe_error(mock_create_portal, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_create_portal.side_effect = Exception("Stripe API error")

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/portal",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 500
    assert response.json()["detail"] == "Error creating portal session"

@patch('app.api.billing.create_checkout_session', new_callable=AsyncMock)
def test_checkout_existing_customer(mock_create_checkout, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_checkout_url = "https://checkout.stripe.com/123"
    mock_create_checkout.return_value = mock_checkout_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/checkout",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"price_lookup_token": "price_123"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_checkout_url
    mock_create_checkout.assert_called_once_with(
        test_team,
        "price_123",
        "http://localhost:3000"
    )

@patch('app.api.billing.create_checkout_session', new_callable=AsyncMock)
def test_checkout_as_system_admin(mock_create_checkout, client, db, test_team, admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_checkout_url = "https://checkout.stripe.com/123"
    mock_create_checkout.return_value = mock_checkout_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"price_lookup_token": "price_123"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_checkout_url
    mock_create_checkout.assert_called_once_with(
        test_team,
        "price_123",
        "http://localhost:3000"
    )

def test_checkout_team_not_found(client, db, admin_token):
    # Arrange
    non_existent_team_id = 999

    # Act
    response = client.post(
        f"/billing/teams/{non_existent_team_id}/checkout",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"price_lookup_token": "price_123"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "Team not found"

@patch('app.api.billing.create_checkout_session', new_callable=AsyncMock)
def test_checkout_stripe_error(mock_create_checkout, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_create_checkout.side_effect = Exception("Stripe API error")

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/checkout",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"price_lookup_token": "price_123"},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 500