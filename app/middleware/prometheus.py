from prometheus_client import Counter, Histogram, Gauge
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from prometheus_fastapi_instrumentator.metrics import Info
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.security import get_current_user_from_auth
from app.db.database import get_db
import time
import logging

logger = logging.getLogger(__name__)

# RED Metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status_code"]
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float("inf"))
)

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

# User Metrics
requests_per_user = Counter(
    "requests_per_user",
    "Number of requests per user",
    ["user_id", "method", "endpoint"]
)

class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip metrics for certain paths
        if request.url.path in ["/metrics", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        start_time = time.time()

        # Process the request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Record RED metrics
        http_requests_total.labels(
            method=request.method,
            endpoint=request.url.path,
            status_code=response.status_code
        ).inc()

        http_request_duration_seconds.labels(
            method=request.method,
            endpoint=request.url.path
        ).observe(duration)

        # Get user ID from request if available
        user_id = None
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
                db = next(get_db())
                user = await get_current_user_from_auth(
                    access_token=access_token if access_token else None,
                    authorization=auth_header if auth_header else None,
                    db=db
                )
                user_id = str(user.id) if user else "anonymous"
                db.close()
        except Exception as e:
            logger.debug(f"Could not get user for metrics: {str(e)}")
            user_id = "anonymous"

        # Record requests per user
        requests_per_user.labels(
            user_id=user_id,
            method=request.method,
            endpoint=request.url.path
        ).inc()

        return response