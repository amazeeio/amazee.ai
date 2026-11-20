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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.audit import prune_audit_log
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


PROVISION_ENDPOINT = "provision-key"

# Second-level labels that sit under a country TLD as part of the public
# suffix (example.co.uk, example.com.au).
# ponytail: heuristic, not the Public Suffix List. Ceilings: a flat ccTLD
# domain such as co.de keeps one count per subdomain, and a shared suffix such
# as onmicrosoft.com puts all its tenants in one count. Swap in tldextract with
# its bundled list if either matters.
_CC_SECOND_LEVEL = {"ac", "co", "com", "edu", "gov", "ne", "net", "or", "org"}


def registrable_domain(domain: str) -> str:
    """Collapse a host to its registrable domain: a.b.bot.store -> bot.store."""
    labels = domain.split(".")
    keep = (
        3
        if len(labels[-1]) == 2 and len(labels) >= 3 and labels[-2] in _CC_SECOND_LEVEL
        else 2
    )
    return ".".join(labels[-keep:])


def enforce_provision_caps(db: Session, email: str) -> None:
    """Record a moad provisioning attempt and raise 429 if a cap is hit.

    Every moad provisioning can mint a new workspace with a demo credit.
    Abusive sign-ups rotate IPs (so a per-IP cap never fires) but tend to use
    one fresh domain. Two caps, both over signup_events rows for this endpoint:
      - per registrable email domain per day, so sibling subdomains
        (a.bot.store, b.bot.store) share one count (free-mail exempt), and
      - global per hour, the backstop for domain rotation.
    Unlike enforce_signup_velocity, rejected attempts are not recorded.
    Soft limits, same count-then-insert trade-off as enforce_signup_velocity.
    """
    from app.services.disposable_domains import extract_domain

    domain = registrable_domain(extract_domain(email))
    now = datetime.now(UTC)
    escaped = domain.replace("%", r"\%").replace("_", r"\_")
    recent = db.query(DBSignupEvent).filter(
        DBSignupEvent.endpoint == PROVISION_ENDPOINT
    )
    exempt = {
        d.strip().lower()
        for d in settings.PROVISION_DOMAIN_CAP_EXEMPT.split(",")
        if d.strip()
    }

    domain_hit = (
        settings.PROVISION_MAX_PER_DOMAIN_PER_DAY > 0
        and domain not in exempt
        and recent.filter(
            DBSignupEvent.created_at >= now - timedelta(days=1),
            or_(
                DBSignupEvent.email.ilike("%@" + escaped, escape="\\"),
                DBSignupEvent.email.ilike("%@%." + escaped, escape="\\"),
            ),
        ).count()
        >= settings.PROVISION_MAX_PER_DOMAIN_PER_DAY
    )
    global_hit = (
        settings.PROVISION_MAX_PER_HOUR > 0
        and recent.filter(DBSignupEvent.created_at >= now - timedelta(hours=1)).count()
        >= settings.PROVISION_MAX_PER_HOUR
    )

    if domain_hit or global_hit:
        logger.warning(
            "Provision cap hit (%s): domain=%s email=%s",
            "domain" if domain_hit else "global",
            domain,
            email,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-ups right now. Please try again later.",
        )

    # Only admitted attempts are recorded: rejected retries must not keep the
    # shared window full and lock out unrelated users (the log line covers them).
    # ponytail: no IP here; the request isn't threaded this deep, and the caps
    # don't use it. Thread it through if forensics need it.
    db.add(DBSignupEvent(email=email, endpoint=PROVISION_ENDPOINT))
    db.commit()


def prune_signup_events(db: Session) -> int:
    """Delete signup_events older than the retention window. Returns rows removed.

    The table is append-only and grows one row per signup attempt, so under
    sustained abuse (the scenario this feature targets) it would accumulate
    indefinitely without cleanup. Only recent rows matter for the velocity
    query, so anything older than SIGNUP_EVENTS_RETENTION_DAYS can be dropped.
    Intended to run from the daily cron alongside the disposable-domain refresh.
    Each run keeps a lasting record of the cleanup as one audit_logs row, also
    when it deletes nothing.
    """
    cutoff = datetime.now(UTC) - timedelta(days=settings.SIGNUP_EVENTS_RETENTION_DAYS)
    deleted = (
        db.query(DBSignupEvent)
        .filter(DBSignupEvent.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.add(
        prune_audit_log(
            table="signup_events",
            deleted=deleted,
            cutoff=cutoff,
            retention_days=settings.SIGNUP_EVENTS_RETENTION_DAYS,
        )
    )
    db.commit()
    logger.info(
        "Pruned %d signup_events older than %d day(s)",
        deleted,
        settings.SIGNUP_EVENTS_RETENTION_DAYS,
    )
    return deleted
