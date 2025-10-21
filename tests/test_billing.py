import pytest
from unittest.mock import patch, AsyncMock
from app.db.models import DBTeamProduct
from fastapi import HTTPException, status

@patch('app.api.billing.create_portal_session', new_callable=AsyncMock)
def test_get_portal_existing_customer(mock_create_portal, client, db, test_team, team_admin_token):
    """
    GIVEN: A team with an existing Stripe customer ID
    WHEN: Creating a portal session without custom return_url
    THEN: The portal session is created with default return URL
    """
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
def test_get_portal_with_custom_return_url(mock_create_portal, client, db, test_team, team_admin_token):
    """
    GIVEN: A team with an existing Stripe customer ID
    WHEN: Creating a portal session with a custom return_url
    THEN: The portal session is created with the provided return URL
    """
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    custom_return_url = "https://example.com/custom/path"
    mock_portal_url = "https://billing.stripe.com/portal/123"
    mock_create_portal.return_value = mock_portal_url

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/portal",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"return_url": custom_return_url},
        follow_redirects=False
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == mock_portal_url
    mock_create_portal.assert_called_once_with(
        "cus_123",
        custom_return_url
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
    assert response.status_code == 400
    assert response.json()["detail"] == "Team has not been registered with Stripe"

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

@patch('app.services.stripe.stripe.api_key', 'sk_test_mock')
@patch('app.api.billing.get_pricing_table_secret')
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

@patch('app.services.stripe.stripe.api_key', 'sk_test_mock')
@patch('app.api.billing.get_pricing_table_secret')
@patch('app.api.billing.create_stripe_customer')
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

@patch('app.services.stripe.stripe.api_key', 'sk_test_mock')
@patch('app.api.billing.get_pricing_table_secret')
def test_get_pricing_table_session_as_system_admin(mock_create_session, client, db, test_team, admin_token):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_client_secret = "cs_test_123"
    mock_create_session.return_value = mock_client_secret

    # Act
    response = client.get(
        f"/billing/teams/{test_team.id}/pricing-table-session",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Assert
    assert response.status_code == 200
    assert response.json()["client_secret"] == mock_client_secret
    mock_create_session.assert_called_once_with("cus_123")

@patch('app.services.stripe.stripe.api_key', 'sk_test_mock')
@patch('app.api.billing.get_pricing_table_secret')
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

# Tests for subscription creation endpoint
@patch('app.api.billing.create_zero_rated_stripe_subscription', new_callable=AsyncMock)
@patch('app.api.billing.create_stripe_customer', new_callable=AsyncMock)
def test_create_team_subscription_success_existing_customer(mock_create_customer, mock_create_subscription, client, db, test_team, test_product, admin_token):
    """Test successful subscription creation for team with existing Stripe customer"""
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.add(test_product)
    db.commit()

    mock_subscription_id = "sub_123"
    mock_create_subscription.return_value = mock_subscription_id

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/subscriptions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"product_id": test_product.id}
    )

    # Assert
    assert response.status_code == 201
    data = response.json()
    assert data["subscription_id"] == mock_subscription_id
    assert data["product_id"] == test_product.id
    assert data["team_id"] == test_team.id
    assert "created_at" in data

    mock_create_subscription.assert_called_once_with(
        customer_id="cus_123",
        product_id=test_product.id
    )
    mock_create_customer.assert_not_called()

@patch('app.api.billing.create_zero_rated_stripe_subscription', new_callable=AsyncMock)
@patch('app.api.billing.create_stripe_customer', new_callable=AsyncMock)
def test_create_team_subscription_success_new_customer(mock_create_customer, mock_create_subscription, client, db, test_team, test_product, admin_token):
    """Test successful subscription creation for team without existing Stripe customer"""
    # Arrange
    test_team.stripe_customer_id = None
    db.add(test_team)
    db.add(test_product)
    db.commit()
    db.refresh(test_product)
    product_id = test_product.id

    mock_customer_id = "cus_new_123"
    mock_subscription_id = "sub_123"
    mock_create_customer.return_value = mock_customer_id
    mock_create_subscription.return_value = mock_subscription_id

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/subscriptions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"product_id": product_id}
    )

    # Assert
    assert response.status_code == 201
    data = response.json()
    assert data["subscription_id"] == mock_subscription_id
    assert data["product_id"] == product_id
    assert data["team_id"] == test_team.id
    assert "created_at" in data

    mock_create_customer.assert_called_once_with(test_team)
    mock_create_subscription.assert_called_once_with(
        customer_id=mock_customer_id,
        product_id=product_id
    )

def test_create_team_subscription_team_not_found(client, db, test_product, admin_token):
    """Test subscription creation with non-existent team"""
    # Arrange
    db.add(test_product)
    db.commit()
    non_existent_team_id = 999

    # Act
    response = client.post(
        f"/billing/teams/{non_existent_team_id}/subscriptions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"product_id": test_product.id}
    )

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "Team not found"

def test_create_team_subscription_product_not_found(client, db, test_team, admin_token):
    """Test subscription creation with non-existent product"""
    # Arrange
    db.add(test_team)
    db.commit()
    non_existent_product_id = "prod_nonexistent"

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/subscriptions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"product_id": non_existent_product_id}
    )

    # Assert
    assert response.status_code == 400
    assert response.json()["detail"] == f"Product with ID {non_existent_product_id} not found in database"

def test_create_team_subscription_unauthorized(client, db, test_team, test_product, team_admin_token):
    """Test subscription creation without system admin privileges"""
    # Arrange
    db.add(test_team)
    db.add(test_product)
    db.commit()

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/subscriptions",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"product_id": test_product.id}
    )

    # Assert
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

@patch('app.api.billing.create_zero_rated_stripe_subscription', new_callable=AsyncMock)
@patch('app.api.billing.create_stripe_customer', new_callable=AsyncMock)
def test_create_team_subscription_stripe_error(mock_create_customer, mock_create_subscription, client, db, test_team, test_product, admin_token):
    """Test subscription creation when Stripe API returns an error"""
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.add(test_product)
    db.commit()

    mock_create_subscription.side_effect = Exception("Stripe API error")

    # Act
    response = client.post(
        f"/billing/teams/{test_team.id}/subscriptions",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"product_id": test_product.id}
    )

    # Assert
    assert response.status_code == 500
    assert "Error creating subscription" in response.json()["detail"]

@patch('app.api.billing.get_subscribed_products_for_customer', new_callable=AsyncMock)
@patch('app.api.billing.cancel_subscription', new_callable=AsyncMock)
def test_delete_subscription_for_team(mock_cancel_subscription, mock_get_subscribed_products_for_customer, client, admin_token, test_team, test_product, db):
    """
    GIVEN: A Team with a product association
    WHEN: The subscription is cancelled
    THEN: The appropriate stripe APIs are called, and the association is removed
    """
    # Associate the product with the team
    team_id=test_team.id,
    product_id=test_product.id
    team_product = DBTeamProduct(
        team_id=team_id,
        product_id=product_id
    )
    db.add(team_product)
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_get_subscribed_products_for_customer.return_value = [("sub_1234", product_id)]

    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/{product_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 200
    mock_get_subscribed_products_for_customer.assert_called_once_with("cus_123")
    mock_cancel_subscription.assert_called_once_with("sub_1234")
    results = db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team_id, DBTeamProduct.product_id == product_id).first()
    assert results is None

def test_delete_subscription_team_not_found(client, admin_token, test_product):
    """
    GIVEN: A non-existent team ID
    WHEN: Attempting to delete a subscription
    THEN: A 404 error is returned
    """
    # Act
    response = client.delete(
        f"/billing/teams/99999/subscription/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 404
    assert "Team not found" in response.json()["detail"]

def test_delete_subscription_product_not_found(client, admin_token, test_team):
    """
    GIVEN: A valid team but non-existent product ID
    WHEN: Attempting to delete a subscription
    THEN: A 400 error is returned
    """
    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/nonexistent_product",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 400
    assert "Product with ID nonexistent_product not found in database" in response.json()["detail"]

def test_delete_subscription_no_association(client, admin_token, test_team, test_product, db):
    """
    GIVEN: A team with no product association
    WHEN: Attempting to delete a subscription
    THEN: A 400 error is returned
    """
    # Setup team with stripe customer but no product association
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 400
    assert f"Team {test_team.id} is not associated with product {test_product.id}" in response.json()["detail"]

def test_delete_subscription_no_stripe_customer(client, admin_token, test_team, test_product, db):
    """
    GIVEN: A team with product association but no stripe customer ID
    WHEN: Attempting to delete a subscription
    THEN: A 400 error is returned
    """
    # Setup product association but no stripe customer
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    test_team.stripe_customer_id = None
    db.add(test_team)
    db.commit()

    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 400
    assert f"Team {test_team.id} is not associated with product {test_product.id}" in response.json()["detail"]

@patch('app.api.billing.get_subscribed_products_for_customer', new_callable=AsyncMock)
@patch('app.api.billing.cancel_subscription', new_callable=AsyncMock)
def test_delete_subscription_stripe_get_products_error(mock_cancel_subscription, mock_get_subscribed_products_for_customer, client, admin_token, test_team, test_product, db):
    """
    GIVEN: A team with valid subscription setup
    WHEN: get_subscribed_products_for_customer raises an exception
    THEN: A 500 error is returned
    """
    # Setup valid subscription
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    # Mock the stripe function to raise an exception
    mock_get_subscribed_products_for_customer.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error calling Stripe API"
        )

    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 500
    assert "Error calling Stripe API" in response.json()["detail"]

@patch('app.api.billing.get_subscribed_products_for_customer', new_callable=AsyncMock)
@patch('app.api.billing.cancel_subscription', new_callable=AsyncMock)
def test_delete_subscription_stripe_cancel_error(mock_cancel_subscription, mock_get_subscribed_products_for_customer, client, admin_token, test_team, test_product, db):
    """
    GIVEN: A team with valid subscription setup
    WHEN: cancel_subscription raises an exception
    THEN: A 500 error is returned
    """
    # Setup valid subscription
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    # Mock successful get but failed cancel
    mock_get_subscribed_products_for_customer.return_value = [("sub_1234", test_product.id)]
    mock_cancel_subscription.side_effect = HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error cancelling subscription in Stripe"
        )

    # Act
    response = client.delete(
        f"/billing/teams/{test_team.id}/subscription/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Assert
    assert response.status_code == 500
    assert "Error cancelling subscription in Stripe" in response.json()["detail"]