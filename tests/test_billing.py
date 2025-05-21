import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi import HTTPException
from app.api.billing import handle_stripe_event_background, get_portal
from app.db.models import DBTeam, DBTeamProduct

@pytest.mark.asyncio
@patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock)
async def test_handle_checkout_session_completed(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id
    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was created
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is not None
    # Verify last payment was updated
    db.refresh(test_team)
    assert test_team.last_payment is not None

@pytest.mark.asyncio
@patch('app.api.billing.get_product_id_from_subscription', new_callable=AsyncMock)
async def test_handle_invoice_payment_succeeded(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

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

    mock_get_product.return_value = test_product.id
    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
    # Verify team-product association was created
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is not None
    # Verify last payment was updated
    db.refresh(test_team)
    assert test_team.last_payment is not None

@pytest.mark.asyncio
@patch('app.api.billing.get_product_id_from_subscription', new_callable=AsyncMock)
async def test_handle_subscription_deleted(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "customer.subscription.deleted"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_subscription.id = "sub_123"
    mock_event.data.object = mock_subscription

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@pytest.mark.asyncio
@patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock)
async def test_handle_checkout_session_completed_team_not_found(mock_get_product, db, test_product):
    # Arrange
    mock_event = Mock()
    mock_event.type = "checkout.session.completed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": "999"}  # Non-existent team ID
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id
    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify no team-product association was created
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

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

@patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_checkout_session_async_payment_succeeded(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.async_payment_succeeded"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was created
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is not None
    # Verify last payment was updated
    db.refresh(test_team)
    assert test_team.last_payment is not None

@patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_checkout_session_async_payment_failed(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.async_payment_failed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.api.billing.get_product_id_from_session', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_checkout_session_expired(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.expired"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.api.billing.get_product_id_from_subscription', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_subscription_payment_failed(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "subscription.payment_failed"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_subscription.id = "sub_123"
    mock_event.data.object = mock_subscription

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.api.billing.get_product_id_from_subscription', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_subscription_paused(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "customer.subscription.paused"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_subscription.id = "sub_123"
    mock_event.data.object = mock_subscription

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.api.billing.get_pricing_table_session', new_callable=AsyncMock)
def test_get_pricing_table_session_existing_customer(mock_get_session, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_client_secret = "cs_test_123"
    mock_get_session.return_value = mock_client_secret

    # Act
    response = client.get(
        f"/billing/teams/{test_team.id}/pricing-table-session",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["client_secret"] == mock_client_secret
    mock_get_session.assert_called_once_with("cus_123")

@patch('app.api.billing.get_pricing_table_session', new_callable=AsyncMock)
@patch('app.api.billing.create_stripe_customer', new_callable=AsyncMock)
def test_get_pricing_table_session_create_customer(mock_create_customer, mock_get_session, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = None
    db.add(test_team)
    db.commit()

    mock_client_secret = "cs_test_123"
    mock_create_customer.return_value = "cus_new_123"
    mock_get_session.return_value = mock_client_secret

    # Act
    response = client.get(
        f"/billing/teams/{test_team.id}/pricing-table-session",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["client_secret"] == mock_client_secret
    mock_create_customer.assert_called_once()
    mock_get_session.assert_called_once_with("cus_new_123")

def test_get_pricing_table_session_team_not_found(client, db, admin_token):
    # Arrange
    non_existent_team_id = 999

    # Act
    response = client.get(
        f"/billing/teams/{non_existent_team_id}/pricing-table-session",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "Team not found"

@patch('app.api.billing.get_pricing_table_session', new_callable=AsyncMock)
def test_get_pricing_table_session_as_system_admin(mock_get_session, client, db, test_team, admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_client_secret = "cs_test_123"
    mock_get_session.return_value = mock_client_secret

    # Act
    response = client.get(
        f"/billing/teams/{test_team.id}/pricing-table-session",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["client_secret"] == mock_client_secret
    mock_get_session.assert_called_once_with("cus_123")

@patch('app.api.billing.get_pricing_table_session', new_callable=AsyncMock)
def test_get_pricing_table_session_stripe_error(mock_get_session, client, db, test_team, team_admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_get_session.side_effect = Exception("Stripe API error")

    # Act
    response = client.get(
        f"/billing/teams/{test_team.id}/pricing-table-session",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )

    # Assert
    assert response.status_code == 500
    assert response.json()["detail"] == "Error creating customer session"