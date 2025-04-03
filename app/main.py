from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from prometheus_client import make_asgi_app
import os
import logging
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from app.middleware.telemetry import TelemetryMiddleware
from app.middleware.audit import AuditLogMiddleware
from app.db.database import get_db
from app.api import auth, private_ai_keys, users, regions, audit, metering
from app.core.config import settings

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("X-Forwarded-Proto") == "https":
            request.scope["scheme"] = "https"
        return await call_next(request)

app = FastAPI(
    title="Private AI Keys as a Service",
    description="""
    Welcome to the Private AI Keys as a Service API! This API allows you to manage your private AI keys.

    ## Getting Started

    Follow these steps to get started with the API:

    1. **Register a new account**
       * Use the `/auth/register` endpoint
       * Provide your email and password

    2. **Login to get access**
       * Use the `/auth/login` endpoint
       * Provide your email and password
       * You'll receive an access token that will be automatically set as a cookie

    3. **Create a Private AI Key**
       * Use the `/private-ai-keys` endpoint
       * Specify the region ID for your key
       * The API will create a new database and return your credentials

    All authenticated endpoints require you to be logged in. The API will automatically use your session cookie
    or you can provide a Bearer token in the Authorization header.
    """,
    version="1.0.0",
    docs_url=None,  # Disable default /docs endpoint
    redoc_url=None,  # Disable default /redoc endpoint
    root_path_in_servers=True,
    server_options={"forwarded_allow_ips": "*"},
    openapi_tags=[
        {
            "name": "Authentication",
            "description": "Operations for user registration, login, and session management"
        },
        {
            "name": "Private AI Keys",
            "description": "Operations for managing your private AI keys"
        }
    ]
)

# Get allowed origins from environment
default_origins = ["http://localhost:8080", "http://localhost:3000", "http://localhost:8800"]
lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
allowed_origins = default_origins + [route.strip() for route in lagoon_routes if route.strip()]

# Add HTTPS redirect middleware first
app.add_middleware(HTTPSRedirectMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS
)

# Add audit logging
app.add_middleware(AuditLogMiddleware, db=next(get_db()))

# Add telemetry
app.add_middleware(TelemetryMiddleware)
# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)
# Add Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(private_ai_keys.router, prefix="/private-ai-keys", tags=["private-ai-keys"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(regions.router, prefix="/regions", tags=["regions"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(metering.router, prefix="/api", tags=["metering"])

@app.get("/", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="API Documentation",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui.css",
        oauth2_redirect_url="/oauth2-redirect",
        init_oauth={
            "usePkceWithAuthorizationCodeGrant": False,
        }
    )

@app.get("/oauth2-redirect", include_in_schema=False)
async def oauth2_redirect():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="OAuth2 Redirect"
    )

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Initialize components if not present
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    # Add security scheme - only Bearer auth
    openapi_schema["components"]["securitySchemes"] = {
        "Bearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your JWT token in the format: Bearer <token>"
        }
    }

    # Ensure schemas are properly initialized
    if "schemas" not in openapi_schema["components"]:
        openapi_schema["components"]["schemas"] = {}

    # Add global security requirement
    openapi_schema["security"] = [{"Bearer": []}]

    # Remove all auth-related parameters and clean up paths
    for path_name, path_item in openapi_schema.get("paths", {}).items():
        for operation in path_item.values():
            # Remove all parameters
            if "parameters" in operation:
                del operation["parameters"]

            # Remove security from non-protected endpoints
            if path_name in ["/auth/login", "/auth/register", "/health"]:
                if "security" in operation:
                    del operation["security"]

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi