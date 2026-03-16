import time
import threading
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import logging

logger = logging.getLogger(__name__)

# Rate limit rules for public endpoints: path -> (max_requests, window_seconds)
# These protect against brute force attacks, spam, and resource abuse.
DEFAULT_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/auth/login": (10, 60),
    "/auth/register": (5, 60),
    "/auth/validate-email": (5, 60),
    "/auth/sign-in": (10, 60),
    "/auth/generate-trial-access": (5, 60),
}

# How many entries to keep in the timestamp map before triggering cleanup.
# When the map grows beyond this threshold old, exhausted keys are pruned.
_CLEANUP_THRESHOLD = 10_000


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware to protect public endpoints from abuse.

    Uses a per-IP sliding window counter. Only requests to the configured
    public auth endpoints are subject to rate limiting.

    NOTE: The client IP is determined from the ``X-Forwarded-For`` header when
    present, falling back to the direct connection address.  This assumes the
    application is deployed behind a trusted reverse proxy that strips any
    client-supplied ``X-Forwarded-For`` headers before forwarding requests.
    """

    def __init__(self, app, rate_limits: dict[str, tuple[int, int]] | None = None):
        super().__init__(app)
        self._rate_limits = rate_limits if rate_limits is not None else DEFAULT_RATE_LIMITS
        self._lock = threading.Lock()
        # {(ip, path): [timestamp, ...]}
        self._request_timestamps: dict[tuple[str, str], list[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        """Return the client IP, honouring X-Forwarded-For when present.

        The first IP in the header is used, as that is the original client IP
        appended by a trusted upstream proxy.  The application must be deployed
        behind a reverse proxy that is configured to strip any client-supplied
        X-Forwarded-For headers.
        """
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check_rate_limit(self, ip: str, path: str, limit: int, window: int) -> tuple[bool, int]:
        """
        Record the current request and check whether the rate limit is exceeded.

        Returns:
            (is_limited, retry_after_seconds)
        """
        now = time.time()
        key = (ip, path)

        with self._lock:
            # Evict timestamps outside the current window
            self._request_timestamps[key] = [
                ts for ts in self._request_timestamps[key] if now - ts < window
            ]

            count = len(self._request_timestamps[key])
            if count >= limit:
                oldest = self._request_timestamps[key][0]
                retry_after = int(window - (now - oldest)) + 1
                return True, retry_after

            # Record this request
            self._request_timestamps[key].append(now)

            # Periodically remove keys whose timestamp lists are now empty to
            # prevent the dictionary from growing without bound.
            if len(self._request_timestamps) > _CLEANUP_THRESHOLD:
                empty_keys = [k for k, v in self._request_timestamps.items() if not v]
                for k in empty_keys:
                    del self._request_timestamps[k]

            return False, 0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path not in self._rate_limits:
            return await call_next(request)

        limit, window = self._rate_limits[path]
        ip = self._get_client_ip(request)

        is_limited, retry_after = self._check_rate_limit(ip, path, limit, window)

        if is_limited:
            logger.warning(f"Rate limit exceeded for IP {ip} on {path}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
