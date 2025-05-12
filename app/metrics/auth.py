from prometheus_client import Counter
from fastapi import Request, Depends
import logging

logger = logging.getLogger(__name__)

# Auth Metrics
auth_attempts_total = Counter(
    "auth_attempts_total",
    "Total number of authentication attempts",
    ["action", "email", "status"]
)

async def track_auth_attempt(request: Request, email: str, status: str):
    """Track authentication attempts using Prometheus metrics."""
    try:
        # Map the endpoint to the appropriate action
        action_map = {
            "/auth/login": "login",
            "/auth/register": "register",
            "/auth/validate-email": "validate_email",
            "/auth/sign-in": "sign_in"
        }
        action = action_map.get(request.url.path, "unknown")

        # Log the metric increment
        logger.info(f"Incrementing auth_attempts_total for {action} - {email} - {status}")

        auth_attempts_total.labels(
            action=action,
            email=email,
            status=status
        ).inc()
    except Exception as e:
        logger.error(f"Could not track auth attempt: {str(e)}", exc_info=True)