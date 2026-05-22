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
