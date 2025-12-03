from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from app.api import auth, private_ai_keys, users, regions, audit, teams, billing, products, pricing_tables, limits
from app.core.config import settings
from app.db.database import get_db
from app.middleware.audit import AuditLogMiddleware
from app.middleware.caching import CacheControlMiddleware
from app.middleware.prometheus import PrometheusMiddleware
from app.middleware.auth import AuthMiddleware
from app.core.worker import monitor_teams, hard_delete_expired_teams
from app.core.locking import try_acquire_lock, release_lock
from app.__version__ import __version__
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import os
import logging
from datetime import UTC

# Set timezone environment variable to prevent tzlocal warning
if not os.environ.get('TZ'):
    os.environ['TZ'] = 'UTC'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.headers.get("X-Forwarded-Proto") == "https":
            request.scope["scheme"] = "https"
        return await call_next(request)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create scheduler
    scheduler = AsyncIOScheduler()

    async def monitor_teams_job():
        db = next(get_db())
        lock_name = "monitor_teams"

        try:
            # Try to acquire the lock
            if try_acquire_lock(lock_name, db, lock_timeout=10):
                logger.info("Acquired monitor_teams lock, executing job")
                try:
                    await monitor_teams(db)
                except Exception as e:
                    logger.error(f"Error in monitor_teams background task: {str(e)}")
                finally:
                    # Always release the lock when done
                    release_lock(lock_name, db)
            else:
                logger.info("Another process has the monitor_teams lock, skipping execution")
        except Exception as e:
            logger.error(f"Error in monitor_teams job: {str(e)}")
            # Try to release lock in case of error
            try:
                release_lock(lock_name, db)
            except Exception as release_error:
                logger.error(f"Error releasing lock: {str(release_error)}")
        finally:
            db.close()

    # Set schedule based on environment
    if settings.ENV_SUFFIX == "local":
        cron_trigger = CronTrigger(minute='*/10', timezone=UTC, jitter=180)
    else:
        # Run every hour in other environments with jitter
        cron_trigger = CronTrigger(hour='*', minute=0, timezone=UTC, jitter=60)

    scheduler.add_job(
        monitor_teams_job,
        trigger=cron_trigger,
        id='monitor_teams',
        replace_existing=True
    )

    # Hard delete job for teams that have been soft-deleted for 60+ days
    async def hard_delete_teams_job():
        db = next(get_db())
        lock_name = "hard_delete_teams"

        try:
            # Try to acquire the lock
            if try_acquire_lock(lock_name, db, lock_timeout=10):
                logger.info("Acquired hard_delete_teams lock, executing job")
                try:
                    await hard_delete_expired_teams(db)
                except Exception as e:
                    logger.error(f"Error in hard_delete_expired_teams background task: {str(e)}")
                finally:
                    # Always release the lock when done
                    release_lock(lock_name, db)
            else:
                logger.info("Another process has the hard_delete_teams lock, skipping execution")
        except Exception as e:
            logger.error(f"Error in hard_delete_teams job: {str(e)}")
            # Try to release lock in case of error
            try:
                release_lock(lock_name, db)
            except Exception as release_error:
                logger.error(f"Error releasing lock: {str(release_error)}")
        finally:
            db.close()

    # Set schedule based on environment for hard delete job
    if settings.ENV_SUFFIX == "local":
        # In local env, run every hour at :30 for testing
        hard_delete_trigger = CronTrigger(hour='*', minute=30, timezone=UTC)
    else:
        # In production, run daily at 3 AM
        hard_delete_trigger = CronTrigger(hour=3, minute=0, timezone=UTC)

    scheduler.add_job(
        hard_delete_teams_job,
        trigger=hard_delete_trigger,
        id='hard_delete_teams',
        replace_existing=True
    )

    # Start the scheduler
    scheduler.start()

    yield

    # Shutdown the scheduler
    scheduler.shutdown()

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
    ],
    lifespan=lifespan
)

# Get allowed origins from environment
default_origins = ["http://localhost:8080", "http://localhost:3000", "http://localhost:8800"]
lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
allowed_origins = default_origins + [route.strip() for route in lagoon_routes if route.strip()]

# Add HTTPS redirect middleware first
app.add_middleware(HTTPSRedirectMiddleware)

# Add Auth middleware (must be before Prometheus and Audit middleware)
app.add_middleware(AuthMiddleware)

# Add Prometheus middleware
app.add_middleware(PrometheusMiddleware)

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
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(private_ai_keys.router, prefix="/private-ai-keys", tags=["private-ai-keys"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(regions.router, prefix="/regions", tags=["regions"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(teams.router, prefix="/teams", tags=["teams"])
app.include_router(billing.router, prefix="/billing", tags=["billing"])
app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(pricing_tables.router, prefix="/pricing-tables", tags=["pricing-tables"])
app.include_router(limits.router, prefix="/limits", tags=["limits"])

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