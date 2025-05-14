from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.security import get_current_user_from_auth
from app.db.database import get_db
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth for certain paths
        if request.url.path in settings.PUBLIC_PATHS:
            return await call_next(request)

        # Initialize user as None
        request.state.user = None

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
                # Get a fresh database session
                db = next(get_db())
                try:
                    user = await get_current_user_from_auth(
                        access_token=access_token if access_token else None,
                        authorization=auth_header if auth_header else None,
                        db=db
                    )
                    # Store essential user data instead of the full SQLAlchemy object
                    request.state.user = {
                        "id": user.id,
                        "email": user.email,
                        "is_admin": user.is_admin,
                        "role": user.role,
                        "team_id": user.team_id
                    }
                except Exception as e:
                    logger.debug(f"Could not get user for request: {str(e)}")
                finally:
                    db.close()
        except Exception as e:
            logger.debug(f"Error in auth middleware: {str(e)}")

        return await call_next(request)