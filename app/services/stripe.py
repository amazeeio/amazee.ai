from typing import Optional, Union
import stripe
import os
import logging
from urllib.parse import urljoin
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import DBTeam, DBSystemSecret

# Configure logger
stripe_logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

async def create_checkout_session(
    team: DBTeam,
    price_lookup_token: str,
    frontend_url: str
) -> str:
    """
    Create a Stripe Checkout Session for team subscription.

    Args:
        team: The team to create the subscription for
        price_lookup_token: Token to identify the specific price
        frontend_url: The frontend URL for success/cancel redirects

    Returns:
        str: The checkout session URL
    """
    try:
        # Fetch the specific price using lookup_keys
        prices = stripe.Price.list(
            active=True,
            lookup_keys=[price_lookup_token],
            expand=['data.product']
        )

        if not prices.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No active subscription price found for token: {price_lookup_token}"
            )

        subscription_price = prices.data[0]

        # Create the checkout session
        checkout_session = stripe.checkout.Session.create(
            customer_email=team.admin_email,
            success_url=f"{frontend_url}/teams/{team.id}/dashboard?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend_url}/teams/{team.id}/pricing",
            mode="subscription",
            line_items=[{
                "price": subscription_price.id,
                "quantity": 1,
            }],
            metadata={
                "team_id": team.id,
                "team_name": team.name,
                "admin_email": team.admin_email
            }
        )

        return checkout_session.url
    except Exception as e:
        stripe_logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating checkout session"
        )

def get_stripe_event( payload: bytes, signature: str, webhook_secret: str) -> stripe.Event:
    """
    Handle Stripe webhook events.

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
        return event

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload"
        )
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature"
        )
    except Exception as e:
        stripe_logger.error(f"Error handling Stripe event: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook"
        )

async def create_portal_session(
    team: DBTeam,
    frontend_url: str
) -> str:
    """
    Create a Stripe Customer Portal session for team subscription management.

    Args:
        team: The team to create the portal session for
        frontend_url: The frontend URL for return redirect

    Returns:
        str: The portal session URL
    """
    try:
        if not team.stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team has no active Stripe subscription"
            )

        # Create the portal session
        portal_session = stripe.billing_portal.Session.create(
            customer=team.stripe_customer_id,
            return_url=f"{frontend_url}/teams/{team.id}/dashboard"
        )

        return portal_session.url
    except Exception as e:
        stripe_logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating portal session"
        )

async def setup_stripe_webhook(webhook_key: str, db: Session) -> None:
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
        base_url = os.getenv("BACKEND_URL", "http://localhost:8800")
        webhook_url = urljoin(base_url, "/api/stripe/handle-event")

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
            stripe_logger.info(f"Deleted existing webhook endpoint: {existing_endpoint.id}")

        # Create new webhook endpoint
        endpoint = stripe.WebhookEndpoint.create(
            url=webhook_url,
            enabled_events=[
                "checkout.session.completed",
                "customer.subscription.deleted"
            ]
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
        stripe_logger.error(f"Error setting up Stripe webhook: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error setting up Stripe webhook"
        )

async def create_stripe_customer(
    team: DBTeam,
    db: Session
) -> str:
    """
    Create a Stripe customer for a team and save the customer ID.

    Args:
        team: The team to create a Stripe customer for
        db: Database session

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

        # Save customer ID to team
        team.stripe_customer_id = customer.id
        db.commit()

        return customer.id

    except Exception as e:
        stripe_logger.error(f"Error creating Stripe customer: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating Stripe customer"
        )
