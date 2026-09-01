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
    budgets,
    internal,
    limits,
    private_ai_keys,
    public,
    regions,
    spend,
    subscription,
    teams,
    users,
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
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.roles import UserRole
from app.core.security import get_current_user_from_auth
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

# Instrument the app. /metrics stays out of the OpenAPI document: it is an
# operational endpoint gated by PROMETHEUS_API_KEY in AuthMiddleware, not part
# of the API surface, and it carries no route dependency to classify it by.
instrumentator.instrument(app).expose(app, include_in_schema=False)


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
app.include_router(
    subscription.router, prefix="/billing/subscription", tags=["billing"]
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
# Authenticating (session cookie, JWT or API token) progressively reveals more
# of the schema. Each operation's tier is derived from its own route
# dependencies, so the mapping stays in sync as endpoints are added. This
# governs schema VISIBILITY only; each endpoint still enforces its own
# authorization.

_TIER_RANK = {"public": 0, "user": 1, "admin": 2}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
# Dependencies that mark an operation as system-admin only.
_ADMIN_DEPENDENCIES = {
    "get_role_min_system_admin",
    "require_system_admin",
    "check_sales_or_higher",
}
# System roles that only privileged staff hold. An RBAC dependency limited to
# these is admin tier even when its callable carries no name to match on.
_ADMIN_ROLES = {UserRole.SYSTEM_ADMIN, UserRole.SALES}


def _operation_tier(dependant) -> str:
    """Classify an operation as public / user / admin from its dependencies."""
    names: set[str] = set()

    # An RBAC dependency used as an instance (Depends(require_system_admin()))
    # has no __name__, so the name patterns below would read it as public.
    # Classify those by the roles they allow instead.
    role_tiers: set[str] = set()

    def _walk(dep) -> None:
        if dep is None:
            return
        call = getattr(dep, "call", None)
        if call is not None:
            names.add(getattr(call, "__name__", ""))
            allowed = getattr(call, "allowed_roles", None)
            if allowed:
                role_tiers.add("admin" if set(allowed) <= _ADMIN_ROLES else "user")
        for sub in getattr(dep, "dependencies", None) or []:
            _walk(sub)

    _walk(dependant)

    if "admin" in role_tiers or names & _ADMIN_DEPENDENCIES:
        return "admin"
    if role_tiers:
        return "user"
    authish = any(
        ("current_user" in name)
        or ("role_min" in name)
        or name.startswith("require_")
        or ("key_creator" in name)
        for name in names
    )
    return "user" if authish else "public"


def _iter_operations(routes):
    """Yield one object per API operation, with its dependencies attached.

    Routes added with include_router are not flattened into app.routes; the
    app holds a router wrapper that resolves its operations on demand. Both
    shapes expose operation_id / unique_id / dependant, which is all the
    tiering needs.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            yield from candidates()


def _operation_id(operation) -> str:
    """The key the OpenAPI document uses for this operation."""
    return getattr(operation, "operation_id", None) or getattr(
        operation, "unique_id", ""
    )


# operationId -> tier, built once from the fully-wired app. operationId is the
# key because it is the one identifier shared by the route object and the
# generated document; matching on paths breaks on converter suffixes.
_OPENAPI_TIERS: dict[str, str] = {
    _operation_id(_operation): _operation_tier(getattr(_operation, "dependant", None))
    for _operation in _iter_operations(app.routes)
}


async def _caller_tier(request: Request, db: Session) -> str:
    """Best-effort caller tier from the credentials on the request.

    Resolution is delegated to the normal auth dependency so that JWTs, API
    tokens and the local bearer token are all recognised. Anything that does
    not resolve to a user is treated as anonymous.
    """
    authorization = request.headers.get("Authorization")
    access_token = request.cookies.get("access_token")
    if not authorization and not access_token:
        return "public"
    try:
        user = await get_current_user_from_auth(
            access_token=access_token,
            authorization=authorization,
            db=db,
            request=request,
        )
    except Exception:
        return "public"
    if user is None:
        return "public"
    return "admin" if user.is_admin else "user"


def _collect_refs(node, found: set[str]) -> None:
    """Gather every component schema name reachable from a schema fragment."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[1])
        for value in node.values():
            _collect_refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _collect_refs(value, found)


def _scope_schema(full: dict, rank: int) -> dict:
    """Return a copy of the schema holding only what this rank may see."""
    schema = deepcopy(full)

    kept_paths: dict = {}
    for path, item in schema.get("paths", {}).items():
        kept_ops: dict = {}
        has_operation = False
        for key, value in item.items():
            if key.lower() in _HTTP_METHODS:
                # An operation missing from the map is treated as admin-only,
                # so a gap makes the document smaller, never wider.
                op_tier = _OPENAPI_TIERS.get(value.get("operationId", ""), "admin")
                if _TIER_RANK[op_tier] <= rank:
                    kept_ops[key] = value
                    has_operation = True
            else:
                kept_ops[key] = value  # keep shared keys (parameters, etc.)
        if has_operation:
            kept_paths[path] = kept_ops
    schema["paths"] = kept_paths

    # Drop the request/response models that only the hidden operations used;
    # their field names would otherwise still describe the hidden endpoints.
    components = schema.get("components", {})
    all_schemas = components.get("schemas") or {}
    if all_schemas:
        reachable: set[str] = set()
        _collect_refs(kept_paths, reachable)
        # A kept model may reference further models, so follow the chain.
        pending = list(reachable)
        while pending:
            name = pending.pop()
            nested: set[str] = set()
            _collect_refs(all_schemas.get(name, {}), nested)
            for new_name in nested - reachable:
                reachable.add(new_name)
                pending.append(new_name)
        components["schemas"] = {
            name: model for name, model in all_schemas.items() if name in reachable
        }

    # Keep only the tag descriptions that still label a visible operation.
    if schema.get("tags"):
        used_tags = {
            tag
            for item in kept_paths.values()
            for key, operation in item.items()
            if key.lower() in _HTTP_METHODS
            for tag in operation.get("tags", [])
        }
        schema["tags"] = [tag for tag in schema["tags"] if tag.get("name") in used_tags]

    return schema


@app.get("/openapi.json", include_in_schema=False)
async def scoped_openapi(request: Request, db: Session = Depends(get_db)):
    rank = _TIER_RANK[await _caller_tier(request, db)]
    return JSONResponse(_scope_schema(app.openapi(), rank))
