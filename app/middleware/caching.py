from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        # Add Cache-Control headers to all responses to prevent caching of sensitive data
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        
        return response
