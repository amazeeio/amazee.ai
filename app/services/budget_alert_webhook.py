"""Outbound delivery of budget threshold alerts.

Posts batches of events to a single configured URL (MOAD, in practice). There is
no retry queue by design: the alert engine only advances its threshold state for
events this module reports as delivered, so a failed POST is re-detected and
re-sent on the next tick. Consumers de-duplicate on ``event_id``.

The destination is a full URL from ``BUDGET_ALERT_WEBHOOK_URL`` rather than a
base plus a hardcoded path, so the receiving route stays the consumer's choice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.core.budget_alert_service import BudgetAlertEvent
from app.core.config import settings

logger = logging.getLogger(__name__)

# Bumped when the event shape changes in a way consumers must notice.
BUDGET_ALERT_API_VERSION = "2026-08-01"

EVENT_TYPE = "budget.threshold_reached"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_event(event: BudgetAlertEvent) -> dict:
    """Render one event for the wire, using our identifiers only.

    The LiteLLM token is deliberately absent: it is a live credential and the
    consumer has no need for it. Our ``key_id`` identifies the key instead.
    """
    return {
        "event_id": event.event_id,
        "type": EVENT_TYPE,
        "created_at": datetime.now(UTC).isoformat(),
        "data": {
            "scope": event.subject_type,
            "threshold_percent": event.threshold_pct,
            "percent_used": event.percent_used,
            "spend": event.spend,
            "max_budget": event.max_budget,
            # Where max_budget came from, so the consumer can word the message:
            # "key_cap" / "litellm_key_budget" = the key's own limit;
            # "team_pool" = the key is uncapped and measured against the team pool;
            # "team_ledger" = a team- or member-scope budget.
            "budget_source": event.budget_source,
            "currency": "USD",
            "budget_duration": event.budget_duration,
            "region": {"id": event.region_id, "name": event.region_name},
            "team": (
                {"id": event.team_id, "name": event.team_name}
                if event.team_id is not None
                else None
            ),
            "user": (
                {"id": event.user_id, "email": event.user_email}
                if event.user_id is not None
                else None
            ),
            "key": (
                {
                    "id": event.key_id,
                    "name": event.key_name,
                    # Service keys belong to the team; user keys have an owner.
                    "is_service_key": event.is_service_key,
                }
                if event.key_id is not None
                else None
            ),
            "period": {
                "key": event.period_key,
                "start": _iso(event.period_start),
                "end": _iso(event.period_end),
            },
        },
    }


def _chunk(events: list[BudgetAlertEvent], size: int):
    size = max(1, size)
    for start in range(0, len(events), size):
        yield events[start : start + size]


async def deliver_events(events: list[BudgetAlertEvent]) -> set[str]:
    """POST events in batches. Returns the ids that were accepted.

    A batch is all-or-nothing: on a non-2xx or a transport error none of its ids
    are returned, so every event in it is retried next tick.
    """
    if not events:
        return set()

    url = settings.BUDGET_ALERT_WEBHOOK_URL
    if not url:
        logger.error(
            "BUDGET_ALERT_WEBHOOK_URL is not set; %d budget alert(s) not delivered",
            len(events),
        )
        return set()

    headers = {"Content-Type": "application/json"}
    if settings.BUDGET_ALERT_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {settings.BUDGET_ALERT_WEBHOOK_TOKEN}"

    delivered: set[str] = set()
    timeout = settings.BUDGET_ALERT_WEBHOOK_TIMEOUT

    async with httpx.AsyncClient(timeout=timeout) as client:
        for batch in _chunk(events, settings.BUDGET_ALERT_BATCH_SIZE):
            payload = {
                "api_version": BUDGET_ALERT_API_VERSION,
                "events": [serialize_event(event) for event in batch],
            }
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.RequestError as exc:
                logger.error(
                    "Budget alert webhook unreachable (%d event(s) will retry): %s",
                    len(batch),
                    exc,
                )
                continue

            if 200 <= response.status_code < 300:
                delivered.update(event.event_id for event in batch)
                logger.info(
                    "Delivered %d budget alert(s) (status %s)",
                    len(batch),
                    response.status_code,
                )
            else:
                logger.error(
                    "Budget alert webhook returned %s (%d event(s) will retry): %s",
                    response.status_code,
                    len(batch),
                    response.text[:500],
                )

    return delivered
