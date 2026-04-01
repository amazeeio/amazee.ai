from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Public models endpoint is intentionally cacheable for 1 hour.
        if request.url.path in {"/public/models", "/public/models/"}:
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            # Add Cache-Control headers to all responses to prevent caching of sensitive data
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, private"
            )

        # Add security headers to all responses
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )

        return response
