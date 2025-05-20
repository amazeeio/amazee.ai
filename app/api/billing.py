from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, BackgroundTasks
from sqlalchemy.orm import Session
import logging
import os
from app.db.database import get_db
from app.core.security import get_current_user_from_auth, check_specific_team_admin
from app.db.models import DBUser, DBTeam, DBSystemSecret
from app.schemas.models import CheckoutSessionCreate
from app.services.stripe import (
    create_checkout_session,
    get_stripe_event,
    create_portal_session,
    get_product_id_from_sub,
    get_product_id_from_session,
    create_stripe_customer
)
from app.core.worker import apply_product_for_team

# Configure logger
logger = logging.getLogger(__name__)
BILLING_WEBHOOK_KEY = "stripe_webhook_secret"
BILLING_WEBHOOK_ROUTE = "/billing/events"

router = APIRouter(
    tags=["billing"]
)

@router.post("/teams/{team_id}/checkout", dependencies=[Depends(check_specific_team_admin)])
async def checkout(
    team_id: int,
    request_data: CheckoutSessionCreate,
    db: Session = Depends(get_db)
):
    """
    Create a Stripe Checkout Session for team subscription.

    Args:
        team_id: The ID of the team to create the subscription for
        request_data: Contains the price_lookup_token to identify the specific price

    Returns:
        redirect to the checkout session
    """
    # Get the team
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    try:
        # Get the frontend URL from environment
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

        # Create checkout session using the service
        checkout_url = await create_checkout_session(team, request_data.price_lookup_token, frontend_url)

        return Response(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": checkout_url}
        )
    except Exception as e:
        logger.error(f"Error creating checkout session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating checkout session"
        )

async def handle_stripe_event_background(event, db: Session):
    """
    Background task to handle Stripe webhook events.
    This runs in a separate thread to avoid blocking the webhook response.
    """
    checkout_session_success = ["checkout.session.async_payment_succeeded", "checkout.session.completed"]
    invoice_success = ["invoice.payment_succeeded"]
    failure_events = ["checkout.session.async_payment_failed", "checkout.session.expired", "subscription.payment_failed", "customer.subscription.deleted"]
    try:
        event_type = event.type
        if not event_type in checkout_session_success + invoice_success + failure_events:
            logger.info(f"Unknown event type: {event_type}")
            return
        event_object = event.data.object
        customer_id = event_object.customer
        if event_type in invoice_success:
            subscription = event_object.parent.subscription_details.subscription
            product_id = await get_product_id_from_sub(subscription)
            await apply_product_for_team(db, customer_id, product_id)
        elif event_type in checkout_session_success:
            product_id = await get_product_id_from_session(event_object.id)
            await apply_product_for_team(db, customer_id, product_id)
        elif event_type in failure_events:
            logger.info(f"Checkout session failed")
            event_object = event.data.object
            logger.info(f"ID: {event_object.id}")
            logger.info(f"Full object: {event_object}")
            # Handle failed checkout
    except Exception as e:
        logger.error(f"Error in background event handler: {str(e)}")

@router.post("/events")
async def handle_events(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events.

    This endpoint processes various Stripe events like subscription updates,
    payment successes, and failures. Events are processed asynchronously in the background.
    """
    try:
        # Get the webhook secret from database
        if os.getenv("WEBHOOK_SIG"):
            webhook_secret = os.getenv("WEBHOOK_SIG")
        else:
            webhook_secret = db.query(DBSystemSecret).filter(
                DBSystemSecret.key == BILLING_WEBHOOK_KEY
            ).first().value

        if not webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stripe webhook secret not configured"
            )

        # Get the raw request body
        payload = await request.body()
        signature = request.headers.get("stripe-signature")

        event = get_stripe_event(payload, signature, webhook_secret)

        # Add the event handling to background tasks
        background_tasks.add_task(handle_stripe_event_background, event, db)

        return Response(
            status_code=status.HTTP_200_OK,
            content="Webhook received and processing started"
        )

    except Exception as e:
        logger.error(f"Error handling Stripe event: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook"
        )

@router.post("/teams/{team_id}/portal", dependencies=[Depends(check_specific_team_admin)])
async def get_portal(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Create a Stripe Customer Portal session for team subscription management and redirect to it.
    If the team doesn't have a Stripe customer ID, one will be created first.

    Args:
        team_id: The ID of the team to create the portal session for

    Returns:
        Redirects to the Stripe Customer Portal URL
    """
    # Get the team
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    try:
        # Create Stripe customer if one doesn't exist
        if not team.stripe_customer_id:
            team.stripe_customer_id = await create_stripe_customer(team, db)

        # Get the frontend URL from environment
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        return_url = f"{frontend_url}/teams/{team.id}/dashboard"

        # Create portal session using the service
        portal_url = await create_portal_session(team.stripe_customer_id, return_url)

        return Response(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": portal_url}
        )
    except Exception as e:
        logger.error(f"Error creating portal session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating portal session"
        )
