import importlib
import logging
import os

from fastapi import HTTPException, status

from app.db.models import DBTeam

logger = logging.getLogger(__name__)
stripe_sdk = importlib.import_module("stripe")
# Backward-compatible alias for tests and existing patch targets.
stripe = stripe_sdk
stripe_sdk.api_key = os.getenv("STRIPE_SECRET_KEY")

# Stripe webhook event type constants
INVOICE_SUCCESS_EVENTS = ["invoice.paid"]
SUBSCRIPTION_SUCCESS_EVENTS = [
    "customer.subscription.resumed",
    "customer.subscription.created",
]
SESSION_SUCCESS_EVENTS = ["checkout.session.completed"]
SESSION_FAILURE_EVENTS = [
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
]
SUBSCRIPTION_FAILURE_EVENTS = [
    "customer.subscription.deleted",
    "customer.subscription.paused",
]
INVOICE_FAILURE_EVENTS = ["invoice.payment_failed"]

SUCCESS_EVENTS = (
    INVOICE_SUCCESS_EVENTS + SUBSCRIPTION_SUCCESS_EVENTS + SESSION_SUCCESS_EVENTS
)
FAILURE_EVENTS = (
    SESSION_FAILURE_EVENTS + SUBSCRIPTION_FAILURE_EVENTS + INVOICE_FAILURE_EVENTS
)
KNOWN_EVENTS = SUCCESS_EVENTS + FAILURE_EVENTS


async def create_portal_session(stripe_customer_id: str, return_url: str) -> str:
    try:
        portal_session = stripe_sdk.billing_portal.Session.create(
            customer=stripe_customer_id, return_url=return_url
        )
        return portal_session.url
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating portal session",
        )


async def create_stripe_customer(team: DBTeam) -> str:
    try:
        if team.stripe_customer_id:
            return team.stripe_customer_id

        customer = stripe_sdk.Customer.create(
            email=team.admin_email,
            name=team.name,
            metadata={"team_id": team.id, "team_name": team.name},
        )
        return customer.id
    except Exception as e:
        logger.error(f"Error creating Stripe customer: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating Stripe customer",
        )


async def create_zero_rated_stripe_subscription(
    customer_id: str, product_id: str, price_id: str = None
) -> str:
    try:
        if not price_id:
            prices = stripe_sdk.Price.list(product=product_id, active=True)
            if not prices.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"No active prices found for product {product_id}",
                )
            if len(prices.data) > 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Multiple prices found for product {product_id}. Free products should have only one price.",
                )
            price_id = prices.data[0].id

        price = stripe_sdk.Price.retrieve(price_id)
        if price.unit_amount != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {product_id} is not free. Price amount: {price.unit_amount} {price.currency}",
            )

        subscription = stripe_sdk.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            payment_behavior="allow_incomplete",
            expand=["latest_invoice"],
        )

        logger.info(
            f"Created free subscription {subscription.id} for customer {customer_id} to product {product_id}"
        )
        return subscription.id
    except HTTPException:
        raise
    except stripe_sdk.error.StripeError as e:
        logger.error(f"Stripe error creating subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error creating subscription: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error creating Stripe subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating subscription",
        )


async def get_product_id_from_subscription(subscription_id: str) -> str:
    """Get the Stripe product ID from a subscription."""
    subscription_items = stripe_sdk.SubscriptionItem.list(
        subscription=subscription_id, expand=["data.price.product"]
    )
    if not subscription_items.data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No items found in subscription",
        )
    return subscription_items.data[0].price.product.id


async def get_product_id_from_session(session_id: str) -> str:
    """Get the Stripe product ID from a checkout session."""
    line_items = stripe_sdk.checkout.Session.list_line_items(session_id)
    return line_items.data[0].price.product


async def get_subscribed_products_for_customer(customer_id: str) -> list[(str, str)]:
    try:
        items = stripe_sdk.Subscription.list(
            customer=customer_id, expand=["data.plan.product"]
        )
    except Exception as e:
        logger.error(
            f"Failed to get subscriptions for customer {customer_id}, {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error calling Stripe API",
        )

    subscriptions = []
    if not items.data:
        logger.warning(f"Found no subscription data for customer {customer_id}")
        return subscriptions

    logger.info(f"Found {len(items.data)} subscriptions for customer {customer_id}")
    for item in items.data:
        subscriptions.append((item.id, item.plan.product.id))
    return subscriptions


async def cancel_subscription(subscription_id: str):
    try:
        stripe_sdk.Subscription.cancel(subscription_id)
    except Exception as e:
        logger.error(f"Failed to cancel subscription {subscription_id}, {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error cancelling subscription in Stripe",
        )


async def get_customer_from_pi(payment_intent: str) -> str:
    payment_intent = stripe_sdk.PaymentIntent.retrieve(payment_intent)
    logger.info("Payment intent is:\n%s", payment_intent)
    return payment_intent.customer


def decode_stripe_event(payload: bytes, signature: str, webhook_secret: str):
    """Decode and verify a Stripe webhook event.

    Returns the parsed Stripe event object.
    """
    try:
        event = stripe_sdk.Webhook.construct_event(payload, signature, webhook_secret)
        logger.info("Decoded event of type: %s", event.type)
        return event
    except stripe_sdk.error.SignatureVerificationError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload"
        )
    except Exception as exc:
        logger.error("Error decoding Stripe event: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        )


async def get_pricing_table_secret(customer_id: str) -> str:
    try:
        session = stripe_sdk.CustomerSession.create(
            customer=customer_id, components={"pricing_table": {"enabled": True}}
        )
        return session.client_secret
    except Exception as e:
        logger.error(f"Error creating customer session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating customer session",
        )
