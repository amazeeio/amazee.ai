import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from app.__version__ import __version__
from app.api import (
    access_groups,
    admin_model_apply,
    admin_models,
    audit,
    auth,
    billing,
    budgets,
    internal,
    limits,
    pricing_tables,
    private_ai_keys,
    products,
    public,
    regions,
    spend,
    subscription,
    teams,
    users,
    webhooks,
)
from app.core.config import settings
from app.middleware.audit import AuditLogMiddleware
from app.middleware.auth import AuthMiddleware
from app.middleware.caching import CacheControlMiddleware
from app.middleware.prometheus import PrometheusMiddleware
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from copy import deepcopy
from jose import jwt
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.api.users import get_user_by_email
from app.core.security import assert_token_not_revoked
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from starlette.middleware.base import BaseHTTPMiddleware

# Set timezone environment variable to prevent tzlocal warning
if not os.environ.get("TZ"):
    os.environ["TZ"] = "UTC"

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("X-Forwarded-Proto") == "https":
            request.scope["scheme"] = "https"
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


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
    version=__version__,
    docs_url=None,  # Disable default /docs endpoint; custom Swagger UI at /
    redoc_url=None,  # Disable default /redoc endpoint
    # Public by design: the schema is the API's docs page (see PUBLIC_PATHS).
    openapi_url=None,  # served per caller by scoped_openapi() below
    root_path_in_servers=True,
    openapi_tags=[
        {
            "name": "Authentication",
            "description": "Operations for user registration, login, and session management",
        },
        {
            "name": "Private AI Keys",
            "description": "Operations for managing your private AI keys",
        },
    ],
    lifespan=lifespan,
)

# Get allowed origins from environment
default_origins = [
    "http://localhost:8080",
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:8800",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:8800",
]


def _normalize_origin(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        logger.warning(
            "Skipping malformed origin (missing scheme/host): %r", url.strip()
        )
        return ""
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin.rstrip("/")


lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
frontend_routes = os.getenv("FRONTEND_ROUTE", "").split(",")
allowed_origins = default_origins + [
    normalized
    for route in lagoon_routes
    if route.strip() and (normalized := _normalize_origin(route))
]
for route in frontend_routes:
    if route.strip():
        normalized = _normalize_origin(route)
        if normalized:
            allowed_origins.append(normalized)

# Add HTTPS redirect middleware first
app.add_middleware(HTTPSRedirectMiddleware)

# Add Auth middleware (must be before Prometheus and Audit middleware)
app.add_middleware(AuthMiddleware)

# Add Prometheus middleware
app.add_middleware(PrometheusMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

# Add trusted host middleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

app.add_middleware(AuditLogMiddleware)
app.add_middleware(CacheControlMiddleware)

# Setup Prometheus instrumentation
instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics"],
    env_var_name="ENABLE_METRICS",
    inprogress_name="fastapi_inprogress",
    inprogress_labels=True,
)

# Add default metrics
instrumentator.add(metrics.default())

# Instrument the app
instrumentator.instrument(app).expose(app)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/version", tags=["system"])
async def get_version():
    return {"version": __version__}


# Include routers
app.include_router(internal.router, prefix="/internal")
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(
    private_ai_keys.router, prefix="/private-ai-keys", tags=["private-ai-keys"]
)
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(regions.router, prefix="/regions", tags=["regions"])
app.include_router(public.router, prefix="/public", tags=["public"])
app.include_router(public.protected_router, tags=["models"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
app.include_router(webhooks.router, prefix="/billing", tags=["webhooks"])
app.include_router(
    subscription.router, prefix="/billing/subscription", tags=["billing"]
)
app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(
    pricing_tables.router, prefix="/pricing-tables", tags=["pricing-tables"]
)
app.include_router(limits.router, prefix="/limits", tags=["limits"])
app.include_router(budgets.router, prefix="/budgets", tags=["budgets"])
app.include_router(spend.router, prefix="/spend", tags=["spend"])
app.include_router(admin_model_apply.router)
app.include_router(admin_models.router)
app.include_router(access_groups.router)


@app.get("/", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="API Documentation",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.31.0/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.31.0/swagger-ui.css",
        oauth2_redirect_url="/oauth2-redirect",
        init_oauth={
            "usePkceWithAuthorizationCodeGrant": False,
        },
    )


@app.get("/oauth2-redirect", include_in_schema=False)
async def oauth2_redirect():
    return get_swagger_ui_oauth2_redirect_html()


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
            "description": "Enter your JWT token in the format: Bearer <token>",
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
            # Remove auth-related parameters injected by dependencies
            if "parameters" in operation:
                operation["parameters"] = [
                    p
                    for p in operation["parameters"]
                    if not (
                        (p.get("in") == "cookie" and p.get("name") == "access_token")
                        or (
                            p.get("in") == "header"
                            and p.get("name", "").lower() == "authorization"
                        )
                    )
                ]
                if not operation["parameters"]:
                    del operation["parameters"]

            # Remove security from non-protected endpoints
            if path_name in [
                "/auth/login",
                "/auth/register",
                "/health",
                "/auth/generate-trial-access",
                "/public/models",
                "/public/models/",
            ]:
                if "security" in operation:
                    del operation["security"]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


# ── Caller-scoped OpenAPI visibility ─────────────────────────────────────────
# `custom_openapi()` builds the full schema; the /openapi.json route below
# returns only the operations the caller is entitled to see:
#   - anonymous callers     -> operations requiring no authentication
#   - authenticated users   -> the above plus operations requiring authentication
#   - system administrators -> the full schema
# Authenticating (session cookie or bearer token) progressively reveals more of
# the schema. Each operation's tier is derived from its own route dependencies,
# so the mapping stays in sync as endpoints are added. This governs schema
# VISIBILITY only; each endpoint still enforces its own authorization.

_TIER_RANK = {"public": 0, "user": 1, "admin": 2}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
# Dependencies that mark an operation as system-admin only.
_ADMIN_DEPENDENCIES = {"get_role_min_system_admin", "require_system_admin"}


def _route_tier(route: APIRoute) -> str:
    """Classify a route as public / user / admin from its auth dependencies."""
    names: set[str] = set()

    def _walk(dep) -> None:
        if dep is None:
            return
        call = getattr(dep, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
        for sub in getattr(dep, "dependencies", None) or []:
            _walk(sub)

    _walk(getattr(route, "dependant", None))

    if names & _ADMIN_DEPENDENCIES:
        return "admin"
    authish = any(
        ("current_user" in name)
        or ("role_min" in name)
        or name.startswith("require_")
        or ("key_creator" in name)
        for name in names
    )
    return "user" if authish else "public"


# (method, path) -> tier, built once from the fully-wired app.
_OPENAPI_TIERS: dict[tuple[str, str], str] = {}
for _route in app.routes:
    if isinstance(_route, APIRoute):
        _tier = _route_tier(_route)
        for _method in _route.methods:
            _OPENAPI_TIERS[(_method.upper(), _route.path)] = _tier


def _caller_tier(request: Request, db: Session) -> str:
    """Best-effort caller tier from the bearer token or session cookie."""
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        return "public"
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert_token_not_revoked(db, payload)
        email = payload.get("sub")
        user = get_user_by_email(db, email) if email else None
    except Exception:
        return "public"
    if not user:
        return "public"
    return "admin" if getattr(user, "is_admin", False) else "user"


@app.get("/openapi.json", include_in_schema=False)
async def scoped_openapi(request: Request, db: Session = Depends(get_db)):
    full = app.openapi()
    rank = _TIER_RANK[_caller_tier(request, db)]
    schema = deepcopy(full)

    kept_paths: dict = {}
    for path, item in schema.get("paths", {}).items():
        kept_ops: dict = {}
        has_operation = False
        for key, value in item.items():
            if key.lower() in _HTTP_METHODS:
                op_tier = _OPENAPI_TIERS.get((key.upper(), path), "user")
                if _TIER_RANK[op_tier] <= rank:
                    kept_ops[key] = value
                    has_operation = True
            else:
                kept_ops[key] = value  # keep shared keys (parameters, etc.)
        if has_operation:
            kept_paths[path] = kept_ops
    schema["paths"] = kept_paths
    return JSONResponse(schema)
