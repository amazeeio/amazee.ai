import logging
import os

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.core.worker import handle_stripe_event_background
from app.db.database import get_db
from app.db.models import DBSystemSecret
from app.services.stripe import decode_stripe_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])

BILLING_WEBHOOK_KEY = "stripe_webhook_secret"


@router.post("/events")
async def handle_events(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Handle Stripe webhook events.

    Verifies the Stripe signature, then dispatches processing to a
    background task so Stripe receives a fast 200 response.
    """
    try:
        # Resolve webhook secret: env var takes precedence, then DB
        if os.getenv("WEBHOOK_SIG"):
            webhook_secret = os.getenv("WEBHOOK_SIG")
        else:
            secret_row = (
                db.query(DBSystemSecret)
                .filter(DBSystemSecret.key == BILLING_WEBHOOK_KEY)
                .first()
            )
            webhook_secret = secret_row.value if secret_row else None

        if not webhook_secret:
            logger.error("Stripe webhook secret not configured")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
            )

        payload = await request.body()
        signature = request.headers.get("stripe-signature")

        event = decode_stripe_event(payload, signature, webhook_secret)

        # Process in the background — use the request-scoped event object
        # and let the background task create its own DB session.
        background_tasks.add_task(handle_stripe_event_background, event)

        return Response(
            status_code=status.HTTP_200_OK,
            content="Webhook received and processing started",
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error handling Stripe event: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        )
