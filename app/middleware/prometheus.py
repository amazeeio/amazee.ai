from prometheus_client import Counter, Histogram
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.security import get_current_user_from_auth
from app.db.database import get_db
import logging

logger = logging.getLogger(__name__)

def normalize_path(path: str) -> str:
    """Replace numeric segments in path with {id}."""
    return '/'.join('{id}' if segment.isdigit() else segment for segment in path.split('/'))

# Audit Metrics
audit_events_total = Counter(
    "audit_events_total",
    "Total number of audit events",
    ["event_type", "resource_type", "request_source", "status_code"]
)

audit_event_duration_seconds = Histogram(
    "audit_event_duration_seconds",
    "Audit event processing duration in seconds",
    ["event_type", "resource_type"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, float("inf"))
)

# User Metrics - grouped by user type and endpoint
requests_by_user_type = Counter(
    "requests_by_user_type",
    "Number of requests grouped by user type",
    ["user_type", "endpoint", "method"]
)

# Auth Metrics - simplified to track success/failure
auth_requests_total = Counter(
    "auth_requests_total",
    "Total number of authentication requests",
    ["endpoint", "status"]  # status will be "success" or "failure"
)

class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip metrics for certain paths
        if request.url.path in ["/metrics", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        # Track auth requests for specific endpoints
        is_auth_endpoint = request.url.path in [
            "/auth/login",
            "/auth/register",
            "/auth/validate-email",
            "/auth/sign-in"
        ]

        response = await call_next(request)

        if is_auth_endpoint:
            auth_requests_total.labels(
                endpoint=request.url.path,
                status="success"
            ).inc()

        # Get user type from request if available
        user_type = "anonymous"
        try:
            # Get access token from cookie or authorization header
            cookies = request.cookies
            headers = request.headers
            access_token = cookies.get("access_token")
            auth_header = headers.get("authorization")

            if auth_header:
                parts = auth_header.split()
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    access_token = parts[1]

            if access_token:
                try:
                    db = next(get_db())
                    user = await get_current_user_from_auth(
                        access_token=access_token if access_token else None,
                        authorization=auth_header if auth_header else None,
                        db=db
                    )
                    if user:
                        # Group users by their role or type
                        user_type = user.role if hasattr(user, 'role') else "authenticated"
                except Exception as e:
                    logger.debug(f"Could not get user for metrics: {str(e)}")
                finally:
                    db.close()
        except Exception as e:
            logger.debug(f"Could not get user for metrics: {str(e)}")

        # Record requests by user type with normalized path
        normalized_path = normalize_path(request.url.path)
        requests_by_user_type.labels(
            user_type=user_type,
            endpoint=normalized_path,
            method=request.method
        ).inc()

        return response
