import logging
from typing import Any

from app.services.stripe import stripe_sdk

logger = logging.getLogger(__name__)


def _marker_value(metadata: Any, key: str):
    return getattr(metadata, key, None) if metadata else None


def _looks_like_new_metadata(metadata: Any) -> bool:
    if not metadata:
        return False
    if _marker_value(metadata, "ai_subscription") == "true":
        return True
    if _marker_value(metadata, "ai_budget_increase"):
        return True
    if _marker_value(metadata, "workspaceId"):
        return True
    return bool(
        _marker_value(metadata, "teamId") and _marker_value(metadata, "regionId")
    )


def _get_event_object(event: Any):
    data = getattr(event, "data", None)
    return getattr(data, "object", None)


def _coerce_subscription_id(subscription_ref: Any) -> str | None:
    if not subscription_ref:
        return None
    if isinstance(subscription_ref, str):
        return subscription_ref
    return getattr(subscription_ref, "id", None)


def _get_subscription_id_from_invoice(event_object: Any) -> str | None:
    subscription_id = _coerce_subscription_id(
        getattr(event_object, "subscription", None)
    )
    if subscription_id:
        return subscription_id

    if hasattr(event_object, "parent"):
        try:
            details = event_object.parent.subscription_details
            return _coerce_subscription_id(getattr(details, "subscription", None))
        except AttributeError:
            return None
    return None


def _get_inline_invoice_metadata(event_object: Any):
    if hasattr(event_object, "parent"):
        try:
            details = event_object.parent.subscription_details
            return getattr(details, "metadata", None)
        except AttributeError:
            return None
    return None


def is_moad_webhook(event: Any) -> bool:
    """Return True when a verified Stripe event belongs to the MOAD/new flow."""
    event_type = getattr(event, "type", "unknown")
    event_object = _get_event_object(event)
    if not event_object:
        return False

    inline_metadatas = [
        getattr(event_object, "metadata", None),
        _get_inline_invoice_metadata(event_object),
    ]
    if any(_looks_like_new_metadata(metadata) for metadata in inline_metadatas):
        return True

    if event_type.startswith("invoice."):
        subscription_id = _get_subscription_id_from_invoice(event_object)
        if not subscription_id:
            return False
        try:
            subscription = stripe_sdk.Subscription.retrieve(subscription_id)
            return _looks_like_new_metadata(getattr(subscription, "metadata", None))
        except Exception as exc:
            logger.warning(
                "Failed to retrieve subscription metadata while classifying webhook %s for subscription %s: %s",
                event_type,
                subscription_id,
                exc,
            )
            raise
    return False
