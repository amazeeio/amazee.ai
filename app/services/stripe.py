import stripe
import os
import logging
from urllib.parse import urljoin
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBSystemSecret
from app.core.config import settings

# Configure logger
logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Full list of possible events: https://docs.stripe.com/api/events/types
INVOICE_SUCCESS_EVENTS = ["invoice.paid"] # Renewal
SUBSCRIPTION_SUCCESS_EVENTS = ["customer.subscription.resumed", "customer.subscription.created"] # New subscription
SESSION_FAILURE_EVENTS = ["checkout.session.async_payment_failed", "checkout.session.expired"] # Checkout failure
SUBSCRIPTION_FAILURE_EVENTS = ["customer.subscription.deleted", "customer.subscription.paused"] # Subscription failure
INVOICE_FAILURE_EVENTS = ["invoice.payment_failed"] # Invoice failure

SUCCESS_EVENTS = INVOICE_SUCCESS_EVENTS + SUBSCRIPTION_SUCCESS_EVENTS
FAILURE_EVENTS = SESSION_FAILURE_EVENTS + SUBSCRIPTION_FAILURE_EVENTS + INVOICE_FAILURE_EVENTS
KNOWN_EVENTS = SUCCESS_EVENTS + FAILURE_EVENTS

def decode_stripe_event( payload: bytes, signature: str, webhook_secret: str) -> stripe.Event:
    """
    Decode Stripe webhook events.

    Args:
        payload: The raw request body
        signature: The Stripe signature header
        webhook_secret: The webhook signing secret

    Returns:
        stripe.Event: The Stripe event
    """
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, webhook_secret
        )
        logger.info(f"Decoded event of type: {event.type}")
        return event

    # If the signature doesn't match, assume bad intent
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload"
        )
    except Exception as e:
        logger.error(f"Error handling Stripe event: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook"
        )

async def create_portal_session(
    stripe_customer_id: str,
    return_url: str
) -> str:
    """
    Create a Stripe Customer Portal session for team subscription management.

    Args:
        stripe_customer_id: The Stripe customer ID to create the portal session for
        frontend_url: The frontend URL for return redirect

    Returns:
        str: The portal session URL
    """
    try:
        # Create the portal session
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=return_url
        )

        return portal_session.url
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating portal session"
        )

async def setup_stripe_webhook(webhook_key: str, webhook_route: str, db: Session) -> None:
    """
    Set up the Stripe webhook endpoint if it doesn't exist and store its signing secret.

    Args:
        webhook_key: The key to store the webhook secret under
        db: Database session
    """
    try:
        # Check if we already have a webhook secret stored
        existing_secret = db.query(DBSystemSecret).filter(
            DBSystemSecret.key == webhook_key
        ).first()

        if existing_secret:
            return

        # Get the base URL from environment
        base_url = settings.main_route
        webhook_url = urljoin(base_url, webhook_route)

        # List existing webhook endpoints
        endpoints = stripe.WebhookEndpoint.list()

        # Check if we already have an endpoint for this URL
        existing_endpoint = None
        for endpoint in endpoints.data:
            if endpoint.url == webhook_url:
                existing_endpoint = endpoint
                break

        if existing_endpoint:
            # For existing endpoints, we need to create a new one to get the secret
            # First delete the old endpoint
            stripe.WebhookEndpoint.delete(existing_endpoint.id)
            logger.info(f"Deleted existing webhook endpoint: {existing_endpoint.id}")

        # Create new webhook endpoint
        endpoint = stripe.WebhookEndpoint.create(
            url=webhook_url,
            enabled_events=KNOWN_EVENTS
        )

        # Store the signing secret
        secret = DBSystemSecret(
            key="stripe_webhook_secret",
            value=endpoint.secret,
            description="Stripe webhook signing secret for handling events"
        )
        db.add(secret)
        db.commit()

    except Exception as e:
        logger.error(f"Error setting up Stripe webhook: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error setting up Stripe webhook, {str(e)}"
        )

async def create_stripe_customer(
    team: DBTeam
) -> str:
    """
    Create a Stripe customer for a team.

    Args:
        team: The team to create a Stripe customer for

    Returns:
        str: The Stripe customer ID

    Raises:
        HTTPException: If error creating customer
    """
    try:
        # Check if team already has a Stripe customer
        if team.stripe_customer_id:
            return team.stripe_customer_id

        # Create Stripe customer
        customer = stripe.Customer.create(
            email=team.admin_email,
            name=team.name,
            metadata={
                "team_id": team.id,
                "team_name": team.name
            }
        )

        return customer.id

    except Exception as e:
        logger.error(f"Error creating Stripe customer: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating Stripe customer"
        )

async def create_zero_rated_stripe_subscription(
    customer_id: str,
    product_id: str,
    price_id: str = None
) -> str:
    """
    Create a Stripe subscription for a customer to a specific free product.

    Args:
        customer_id: The Stripe customer ID
        product_id: The Stripe product ID
        price_id: Optional price ID. If not provided, will use the default price for the product

    Returns:
        str: The Stripe subscription ID

    Raises:
        HTTPException: If error creating subscription or if product is not free
    """
    try:
        # If no price_id provided, get the default price for the product
        if not price_id:
            prices = stripe.Price.list(
                product=product_id,
                active=True
            )

            if not prices.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No active prices found for product {product_id}"
                )

            # Validate that there is only one price for free products
            if len(prices.data) > 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Multiple prices found for product {product_id}. Free products should have only one price."
                )

            price_id = prices.data[0].id

        # Get the price details to validate it's free
        price = stripe.Price.retrieve(price_id)

        # Validate that the price is zero (free)
        if price.unit_amount != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product_id} is not free. Price amount: {price.unit_amount} {price.currency}"
            )

        # Create the subscription for free product
        subscription = stripe.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            payment_behavior="allow_incomplete",
            expand=["latest_invoice"]
        )

        logger.info(f"Created free subscription {subscription.id} for customer {customer_id} to product {product_id}")
        return subscription.id

    except HTTPException:
        # Re-raise HTTPExceptions as-is (validation errors, etc.)
        raise
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error creating subscription: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error creating Stripe subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating subscription"
        )

async def get_product_id_from_subscription(subscription_id: str) -> str:
    """
    Get the Stripe product ID for the team's subscription.

    Args:
        subscription_id: The Stripe subscription ID

    Returns:
        str: The Stripe product ID
    """
    # Get the list of subscription items
    subscription_items = stripe.SubscriptionItem.list(
        subscription=subscription_id,
        expand=['data.price.product']
    )

    if not subscription_items.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No items found in subscription"
        )

    return subscription_items.data[0].price.product.id

async def get_product_id_from_session(session_id: str) -> str:
    """
    Get the Stripe product ID for the team's subscription from a checkout session.

    Args:
        session_id: The Stripe checkout session ID
    """
    line_items = stripe.checkout.Session.list_line_items(session_id)
    return line_items.data[0].price.product

async def get_customer_from_pi(payment_intent: str) -> str:
    """
    Get the Stripe customer ID from a payment intent.
    """
    payment_intent = stripe.PaymentIntent.retrieve(payment_intent)
    logger.info(f"Payment intent is:\n{payment_intent}")
    return payment_intent.customer

async def get_pricing_table_secret(customer_id: str) -> str:
    """
    Create a Stripe Customer Session client secret for a customer.

    Args:
        customer_id: The Stripe customer ID to create the session for

    Returns:
        str: The customer session client secret
    """
    try:
        # Create the customer session
        session = stripe.CustomerSession.create(
            customer=customer_id,
            components={
                "pricing_table": {"enabled": True}
            }
        )

        return session.client_secret
    except Exception as e:
        logger.error(f"Error creating customer session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating customer session"
        )
