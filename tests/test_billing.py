import pytest
from unittest.mock import Mock, patch, AsyncMock
from app.api.billing import handle_stripe_event_background
from app.db.models import DBTeamProduct
from stripe._customer_session import CustomerSession

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