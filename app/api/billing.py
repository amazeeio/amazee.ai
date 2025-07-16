from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, BackgroundTasks
from sqlalchemy.orm import Session
import logging
import os
from datetime import datetime, UTC
from app.db.database import get_db
from app.core.security import check_specific_team_admin, check_system_admin
from app.db.models import DBTeam, DBSystemSecret, DBProduct, DBTeamProduct
from app.schemas.models import PricingTableSession, SubscriptionCreate, SubscriptionResponse
from app.services.stripe import (
    decode_stripe_event,
    create_portal_session,
    create_stripe_customer,
    get_pricing_table_secret,
    create_zero_rated_stripe_subscription,
)
from app.core.worker import handle_stripe_event_background

# Configure logger
logger = logging.getLogger(__name__)
BILLING_WEBHOOK_KEY = "stripe_webhook_secret"
BILLING_WEBHOOK_ROUTE = "/billing/events"

router = APIRouter(
    tags=["billing"]
)

# TODO: Verify where we want this to be
def get_return_url(team_id: int) -> str:
    """
    Get the return URL for the team dashboard.

    Args:
        team_id: The ID of the team to get the return URL for

    Returns:
        The return URL for the team dashboard
    """
    # Get the frontend URL from environment
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return f"{frontend_url}/teams/{team_id}/dashboard"


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
        # Get the webhook secret from database or environment variable
        if os.getenv("WEBHOOK_SIG"):
            webhook_secret = os.getenv("WEBHOOK_SIG")
        else:
            webhook_secret = db.query(DBSystemSecret).filter(
                DBSystemSecret.key == BILLING_WEBHOOK_KEY
            ).first().value

        if not webhook_secret:
            logger.error("Stripe webhook secret not configured")
            # 404 for security reasons - if we're not accepting traffic here, then it doesn't exist
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not found"
            )

        # Get the raw request body
        payload = await request.body()
        signature = request.headers.get("stripe-signature")

        event = decode_stripe_event(payload, signature, webhook_secret)

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
    if not team.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team has not been registered with Stripe"
        )

    try:
        return_url = get_return_url(team_id)
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

@router.get("/teams/{team_id}/pricing-table-session", dependencies=[Depends(check_specific_team_admin)], response_model=PricingTableSession)
async def get_pricing_table_session(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Create a Stripe Customer Session client secret for team subscription management.
    If the team doesn't have a Stripe customer ID, one will be created first.

    Args:
        team_id: The ID of the team to create the customer session for

    Returns:
        JSON response containing the client secret
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
            logger.info(f"Creating Stripe customer for team {team.id}")
            team.stripe_customer_id = await create_stripe_customer(team)
            db.add(team)
            db.commit()

        logger.info(f"Stripe ID is {team.stripe_customer_id}")
        # Create customer session using the service
        client_secret = await get_pricing_table_secret(team.stripe_customer_id)

        return PricingTableSession(client_secret=client_secret)
    except Exception as e:
        logger.error(f"Error creating customer session: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error creating customer session"
        )

@router.post("/teams/{team_id}/subscriptions", dependencies=[Depends(check_system_admin)], response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_team_subscription(
    team_id: int,
    subscription_data: SubscriptionCreate,
    db: Session = Depends(get_db)
):
    """
    Create a subscription for a specific team. Only accessible by system admin users.

    Args:
        team_id: The ID of the team to create the subscription for
        subscription_data: The subscription data containing the Stripe product ID

    Returns:
        JSON response containing the subscription details
    """
    # Get the team
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    # Check if the product exists in the database
    product = db.query(DBProduct).filter(DBProduct.id == subscription_data.product_id).first()
    if not product:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product with ID {subscription_data.product_id} not found in database"
        )

        # Check if the team is already subscribed to any product
    existing_subscription = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == team_id
    ).first()

    if existing_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Team {team_id} is already subscribed to a product"
        )

    try:
        # Create Stripe customer if one doesn't exist
        if not team.stripe_customer_id:
            logger.info(f"Creating Stripe customer for team {team.id}")
            team.stripe_customer_id = await create_stripe_customer(team)
            db.add(team)
            db.commit()

        # Create the Stripe subscription
        subscription_id = await create_zero_rated_stripe_subscription(
            customer_id=team.stripe_customer_id,
            product_id=subscription_data.product_id
        )

        logger.info(f"Created subscription {subscription_id} for team {team.id} to product {subscription_data.product_id}")

        return SubscriptionResponse(
            subscription_id=subscription_id,
            product_id=subscription_data.product_id,
            team_id=team_id,
            created_at=datetime.now(UTC)
        )

    except Exception as e:
        logger.error(f"Error creating subscription for team {team_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating subscription: {str(e)}"
        )
