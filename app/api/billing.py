from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy.orm import Session
import logging
import os
from pydantic import BaseModel

from app.db.database import get_db
from app.core.security import get_current_user_from_auth, check_specific_team_admin
from app.db.models import DBUser, DBTeam, DBSystemSecret
from app.schemas.models import CheckoutSessionCreate
from app.services.stripe import (
    create_checkout_session,
    handle_stripe_event,
    create_portal_session
)

# Configure logger
logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["billing"]
)

@router.post("/teams/{team_id}/checkout", dependencies=[Depends(check_specific_team_admin)])
async def checkout(
    team_id: int,
    request_data: CheckoutSessionCreate,
    request: Request,
    current_user: DBUser = Depends(get_current_user_from_auth),
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
    try:
        # Get the team
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

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

@router.post("/events")
async def handle_events(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handle Stripe webhook events.

    This endpoint processes various Stripe events like subscription updates,
    payment successes, and failures.
    """
    try:
        # Get the webhook secret from database
        webhook_secret = db.query(DBSystemSecret).filter(
            DBSystemSecret.key == "stripe_webhook_secret"
        ).first()

        if not webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Stripe webhook secret not configured"
            )

        # Get the raw request body
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature")

        # Handle the event using the service
        await handle_stripe_event(payload, sig_header, webhook_secret.value, db)

        return Response(
            status_code=status.HTTP_200_OK,
            content="Webhook processed successfully"
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
    request: Request,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db)
):
    """
    Create a Stripe Customer Portal session for team subscription management and redirect to it.

    Args:
        team_id: The ID of the team to create the portal session for

    Returns:
        Redirects to the Stripe Customer Portal URL
    """
    try:
        # Get the team
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found"
            )

        # Get the frontend URL from environment
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

        # Create portal session using the service
        portal_url = await create_portal_session(team, frontend_url)

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
