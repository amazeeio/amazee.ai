"""Per-IP signup velocity limiting (trial-account abuse protection, moad #620).

The public, unauthenticated signup endpoints (validate-email, sign-in, register,
generate-trial-access) can create users/teams for anyone. A single actor created
dozens of trial teams in a day. This caps how many signup attempts one client IP
may make within a rolling window, backed by the append-only ``signup_events``
table (no Redis in this backend; the shared DB keeps the cap correct across pods).

Per-IP (not per-email-domain) is deliberate: legit users share providers like
gmail.com, and moad tags trial emails as ``base+<team_id>@<real-domain>``, so a
per-domain cap would false-positive on shared domains.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DBSignupEvent

logger = logging.getLogger(__name__)


def client_ip(request: Optional[Request]) -> Optional[str]:
    """Best-effort client IP. Consistent with the audit middleware: uvicorn is
    started with --forwarded-allow-ips, so request.client already reflects the
    real client behind the ingress."""
    if request is None or request.client is None:
        return None
    return request.client.host


def enforce_signup_velocity(
    request: Optional[Request],
    db: Session,
    email: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> None:
    """Record the signup attempt and raise 429 if this IP is over its per-window cap.

    The attempt is always recorded (append-only), so sustained abuse keeps the
    window saturated. Missing IPs (unknown client) are not limited, only logged.
    """
    if not settings.ENABLE_SIGNUP_VELOCITY_LIMIT:
        return

    ip = client_ip(request)
    if not ip:
        logger.warning("Signup velocity: no client IP for endpoint=%s", endpoint)
        return

    window_start = datetime.now(UTC) - timedelta(
        minutes=settings.SIGNUP_VELOCITY_WINDOW_MINUTES
    )
    recent = (
        db.query(DBSignupEvent)
        .filter(
            DBSignupEvent.ip_address == ip,
            DBSignupEvent.created_at >= window_start,
        )
        .count()
    )

    # Always record the attempt (keeps the window saturated under hammering).
    db.add(DBSignupEvent(ip_address=ip, email=email, endpoint=endpoint))
    db.commit()

    # NOTE: the count above and the insert here are not atomic, so the cap is a
    # soft limit, not a hard one. Under a burst of concurrent requests from one
    # IP, several can observe recent == cap - 1 before any commits, letting a
    # handful through at the boundary. This is acceptable for abuse mitigation
    # (impact is bounded to a few extra signups) and is the trade-off of a
    # DB-backed limiter without Redis / SELECT ... FOR UPDATE. Do not assume this
    # blocks exactly SIGNUP_MAX_PER_IP_PER_WINDOW attempts.
    if recent >= settings.SIGNUP_MAX_PER_IP_PER_WINDOW:
        logger.warning(
            "Signup velocity cap hit: ip=%s recent=%d cap=%d endpoint=%s",
            ip,
            recent,
            settings.SIGNUP_MAX_PER_IP_PER_WINDOW,
            endpoint,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many signup attempts from this network. Please try again later.",
        )


def prune_signup_events(db: Session) -> int:
    """Delete signup_events older than the retention window. Returns rows removed.

    The table is append-only and grows one row per signup attempt, so under
    sustained abuse (the scenario this feature targets) it would accumulate
    indefinitely without cleanup. Only recent rows matter for the velocity
    query, so anything older than SIGNUP_EVENTS_RETENTION_DAYS can be dropped.
    Intended to run from the daily cron alongside the disposable-domain refresh.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.SIGNUP_EVENTS_RETENTION_DAYS)
    deleted = (
        db.query(DBSignupEvent)
        .filter(DBSignupEvent.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(
        "Pruned %d signup_events older than %d day(s)",
        deleted,
        settings.SIGNUP_EVENTS_RETENTION_DAYS,
    )
    return deleted
