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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.worker import handle_stripe_event_background
from app.db.database import get_db
from app.db.models import DBStripeProcessedEvent, DBSystemSecret
from app.services.stripe import decode_stripe_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["billing"])

BILLING_WEBHOOK_KEY = "stripe_webhook_secret"
LEGACY_STRIPE_DRAIN_MODE_ENV = "LEGACY_STRIPE_DRAIN_MODE"


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
        # Temporary post-migration safety valve: once PROD billing has moved to
        # MOAD's /cycle flow, the legacy amazee.ai webhook should acknowledge
        # any late/retried Stripe deliveries without mutating legacy billing
        # state. This buys time for manual subscription cancellation in Stripe.
        if os.getenv(LEGACY_STRIPE_DRAIN_MODE_ENV, "").lower() in ("1", "true", "yes"):
            logger.warning(
                "Stripe webhook drain mode enabled; acknowledging event without processing"
            )
            return Response(
                status_code=status.HTTP_200_OK,
                content="Webhook acknowledged by drain mode",
            )

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

        # Claim-row idempotency: insert the event ID before dispatching
        # the background task. If a duplicate webhook arrives concurrently,
        # the UniqueViolation on stripe_event_id means the event is already
        # being processed — return 200 immediately (Stripe doesn't retry on 200).
        #
        # NOTE: use getattr(), not event.get(). Since stripe-python v15,
        # StripeObject no longer subclasses dict, so event.get("id") raises
        # AttributeError("get"). The worker already uses attribute access.
        event_id = getattr(event, "id", None)
        event_type = getattr(event, "type", "unknown")
        if event_id:
            try:
                db.add(
                    DBStripeProcessedEvent(
                        stripe_event_id=event_id, event_type=event_type
                    )
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info("Webhook event already claimed: event_id=%s", event_id)
                return Response(
                    status_code=status.HTTP_200_OK,
                    content="Webhook already processed",
                )

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
