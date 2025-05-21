from prometheus_client import Counter, Histogram
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging
from app.core.config import settings

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
        if request.url.path in settings.PUBLIC_PATHS:
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

        # Get user type from request state (set by AuthMiddleware)
        user_type = "anonymous"
        if hasattr(request.state, 'user') and request.state.user:
            # Group users by their role or type
            if hasattr(request.state.user, 'role'):
                user_type = request.state.user.role
            elif hasattr(request.state.user, 'is_admin') and request.state.user.is_admin:
                user_type = "system_admin"
            else:
                user_type = "authenticated"

        # Record requests by user type with normalized path
        normalized_path = normalize_path(request.url.path)
        requests_by_user_type.labels(
            user_type=user_type,
            endpoint=normalized_path,
            method=request.method
        ).inc()

        return response
