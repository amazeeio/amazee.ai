from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session
from app.db.models import DBAuditLog
from app.api.auth import get_current_user_from_auth
from app.db.database import get_db
from app.middleware.prometheus import audit_events_total, audit_event_duration_seconds
import json
import logging
import time
from fastapi import Cookie, Header
from typing import Optional

logger = logging.getLogger(__name__)

class AuditLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, db: Session):
        super().__init__(app)
        self.db = db

    async def dispatch(self, request: Request, call_next):
        # Skip audit logging for certain paths
        if request.url.path in ["/health", "/docs", "/openapi.json", "/audit/logs", "/auth/me", "/metrics"]:
            return await call_next(request)

        start_time = time.time()

        # Get the response
        response = await call_next(request)

        try:
            # Get a fresh database session for each request
            db = next(get_db())

            # Get user_id from request state (set by AuthMiddleware)
            user_id = None
            if hasattr(request.state, 'user') and request.state.user:
                if isinstance(request.state.user, dict):
                    user_id = request.state.user.get('id')
                else:
                    user_id = request.state.user.id

            # Extract path parameters
            path_params = request.path_params
            resource_id = next(iter(path_params.values()), None) if path_params else None

            # Determine request source
            request_source = None
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")

            # Check if request is from our frontend
            if origin or referer:
                request_source = "frontend"
            else:
                # If no origin/referer and has auth header, likely direct API call
                request_source = "api" if request.headers.get("authorization") else None

            # Get resource type from path
            resource_type = request.url.path.split("/")[1]  # First path segment

            # Create audit log entry
            audit_log = DBAuditLog(
                user_id=user_id,
                event_type=request.method,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id else None,
                action=f"{request.method} {request.url.path}",
                details={
                    "path": request.url.path,
                    "query_params": dict(request.query_params),
                    "status_code": response.status_code
                },
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                request_source=request_source
            )

            db.add(audit_log)
            db.commit()

            # Record audit metrics
            audit_events_total.labels(
                event_type=request.method,
                resource_type=resource_type,
                request_source=request_source or "unknown",
                status_code=response.status_code
            ).inc()

            # Record audit event duration
            duration = time.time() - start_time
            audit_event_duration_seconds.labels(
                event_type=request.method,
                resource_type=resource_type
            ).observe(duration)

        except Exception as e:
            logger.error(f"Failed to create audit log: {str(e)}", exc_info=True)
            # Don't re-raise the exception - we don't want to break the request if audit logging fails
            pass
        finally:
            if 'db' in locals():
                db.close()

        return response