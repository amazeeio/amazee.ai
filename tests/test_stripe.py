import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.stripe import create_stripe_subscription
from fastapi import HTTPException
import stripe

"""
Test cases for create_stripe_subscription function

Given: A Stripe service with create_stripe_subscription function
When: Creating subscriptions for free products
Then: Validate business rules and handle various scenarios
"""

@patch('app.services.stripe.stripe.Price.list')
@patch('app.services.stripe.stripe.Price.retrieve')
@patch('app.services.stripe.stripe.Subscription.create')
def test_create_stripe_subscription_success_no_price_id(
    mock_subscription_create,
    mock_price_retrieve,
    mock_price_list
):
    """
    Given: A customer and product with a single free price
    When: Creating a subscription without specifying price_id
    Then: Successfully create the subscription using the default price
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock price list response
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(id="price_123456789")
    ]
    mock_price_list.return_value = mock_response

    # Mock price retrieve response
    mock_price = MagicMock()
    mock_price.unit_amount = 0
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Mock subscription create response
    mock_subscription = MagicMock()
    mock_subscription.id = "sub_123456789"
    mock_subscription_create.return_value = mock_subscription

    # Act
    result = asyncio.run(create_stripe_subscription(customer_id, product_id))

    # Assert
    assert result == "sub_123456789"
    mock_price_list.assert_called_once_with(product=product_id, active=True)
    mock_price_retrieve.assert_called_once_with("price_123456789")
    mock_subscription_create.assert_called_once_with(
        customer=customer_id,
        items=[{"price": "price_123456789"}],
        payment_behavior="allow_incomplete",
        expand=["latest_invoice"]
    )

@patch('app.services.stripe.stripe.Price.retrieve')
@patch('app.services.stripe.stripe.Subscription.create')
def test_create_stripe_subscription_success_with_price_id(
    mock_subscription_create,
    mock_price_retrieve
):
    """
    Given: A customer, product, and specific price_id for a free product
    When: Creating a subscription with a specific price_id
    Then: Successfully create the subscription using the specified price
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"
    price_id = "price_987654321"

    # Mock price retrieve response
    mock_price = MagicMock()
    mock_price.unit_amount = 0
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Mock subscription create response
    mock_subscription = MagicMock()
    mock_subscription.id = "sub_987654321"
    mock_subscription_create.return_value = mock_subscription

    # Act
    result = asyncio.run(create_stripe_subscription(customer_id, product_id, price_id))

    # Assert
    assert result == "sub_987654321"
    mock_price_retrieve.assert_called_once_with(price_id)
    mock_subscription_create.assert_called_once_with(
        customer=customer_id,
        items=[{"price": price_id}],
        payment_behavior="allow_incomplete",
        expand=["latest_invoice"]
    )

@patch('app.services.stripe.stripe.Price.list')
def test_create_stripe_subscription_no_active_prices(mock_price_list):
    """
    Given: A product with no active prices
    When: Creating a subscription without specifying price_id
    Then: Raise HTTPException with appropriate error message
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock empty price list response
    mock_response = MagicMock()
    mock_response.data = []
    mock_price_list.return_value = mock_response

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"No active prices found for product {product_id}"

@patch('app.services.stripe.stripe.Price.list')
def test_create_stripe_subscription_multiple_prices(mock_price_list):
    """
    Given: A product with multiple active prices
    When: Creating a subscription without specifying price_id
    Then: Raise HTTPException indicating free products should have only one price
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock multiple prices response
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(id="price_1"),
        MagicMock(id="price_2")
    ]
    mock_price_list.return_value = mock_response

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"Multiple prices found for product {product_id}. Free products should have only one price."

@patch('app.services.stripe.stripe.Price.list')
@patch('app.services.stripe.stripe.Price.retrieve')
def test_create_stripe_subscription_non_free_product(mock_price_retrieve, mock_price_list):
    """
    Given: A product with a non-zero price
    When: Creating a subscription
    Then: Raise HTTPException indicating the product is not free
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock price list response
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(id="price_123456789")
    ]
    mock_price_list.return_value = mock_response

    # Mock price retrieve response with non-zero amount
    mock_price = MagicMock()
    mock_price.unit_amount = 1000  # $10.00
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"Product {product_id} is not free. Price amount: 1000 usd"

@patch('app.services.stripe.stripe.Price.retrieve')
def test_create_stripe_subscription_with_price_id_non_free(mock_price_retrieve):
    """
    Given: A specific price_id with non-zero amount
    When: Creating a subscription with that price_id
    Then: Raise HTTPException indicating the product is not free
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"
    price_id = "price_987654321"

    # Mock price retrieve response with non-zero amount
    mock_price = MagicMock()
    mock_price.unit_amount = 2000  # $20.00
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id, price_id))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"Product {product_id} is not free. Price amount: 2000 usd"

@patch('app.services.stripe.stripe.Price.list')
@patch('app.services.stripe.stripe.Price.retrieve')
@patch('app.services.stripe.stripe.Subscription.create')
def test_create_stripe_subscription_stripe_error(
    mock_subscription_create,
    mock_price_retrieve,
    mock_price_list
):
    """
    Given: Valid free product but Stripe API error during subscription creation
    When: Creating a subscription
    Then: Raise HTTPException with Stripe error details
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock price list response
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(id="price_123456789")
    ]
    mock_price_list.return_value = mock_response

    # Mock price retrieve response
    mock_price = MagicMock()
    mock_price.unit_amount = 0
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Mock Stripe error
    mock_subscription_create.side_effect = stripe.error.StripeError("Customer not found")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id))

    assert exc_info.value.status_code == 400
    assert "Error creating subscription: Customer not found" in exc_info.value.detail

@patch('app.services.stripe.stripe.Price.list')
@patch('app.services.stripe.stripe.Price.retrieve')
@patch('app.services.stripe.stripe.Subscription.create')
def test_create_stripe_subscription_general_exception(
    mock_subscription_create,
    mock_price_retrieve,
    mock_price_list
):
    """
    Given: Valid free product but unexpected error during subscription creation
    When: Creating a subscription
    Then: Raise HTTPException with generic error message
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"

    # Mock price list response
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(id="price_123456789")
    ]
    mock_price_list.return_value = mock_response

    # Mock price retrieve response
    mock_price = MagicMock()
    mock_price.unit_amount = 0
    mock_price.currency = "usd"
    mock_price_retrieve.return_value = mock_price

    # Mock general exception
    mock_subscription_create.side_effect = Exception("Unexpected error")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Error creating subscription"

@patch('app.services.stripe.stripe.Price.retrieve')
def test_create_stripe_subscription_price_retrieve_error(mock_price_retrieve):
    """
    Given: A price_id that cannot be retrieved
    When: Creating a subscription with that price_id
    Then: Raise HTTPException with Stripe error details
    """
    # Arrange
    customer_id = "cus_123456789"
    product_id = "prod_123456789"
    price_id = "price_invalid"

    # Mock Stripe error during price retrieval
    mock_price_retrieve.side_effect = stripe.error.StripeError("Price not found")

    # Act & Assert
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_stripe_subscription(customer_id, product_id, price_id))

    assert exc_info.value.status_code == 400
    assert "Error creating subscription: Price not found" in exc_info.value.detail