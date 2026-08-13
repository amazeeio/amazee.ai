from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field, field_validator
import os


class Settings(BaseSettings):
    # Database settings
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres/postgres_service"
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "50"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "50"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

    # JWT settings
    # Bind ONLY to AMAZEEAI_JWT_SECRET. Using an explicit validation_alias stops
    # a bare SECRET_KEY env var (e.g. the Helm default) from silently overriding
    # the real signing key. Required: startup fails if the secret is unset.
    SECRET_KEY: str = Field(validation_alias="AMAZEEAI_JWT_SECRET")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # Increase to 60 minutes

    # CORS settings
    CORS_ORIGINS: list[str] = [
        "http://localhost:8080",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:8800",
    ]
    ALLOWED_HOSTS: list[str] = ["*"]  # In production, restrict this
    # NOTE: which client IPs uvicorn trusts X-Forwarded-* headers from is set via
    # the FORWARDED_ALLOW_IPS env var, passed to uvicorn's --forwarded-allow-ips
    # in backend-start.sh (a FastAPI constructor kwarg does NOT reach uvicorn).
    PUBLIC_PATHS: list[str] = [
        "/health",
        # The schema and the Swagger UI at / are the public API docs.
        "/openapi.json",
        "/",
        "/public/models",
        "/public/models/",
    ]

    AWS_ACCESS_KEY_ID: str = "AKIATEST"
    AWS_SECRET_ACCESS_KEY: str = "sk-string"
    SES_SENDER_EMAIL: str = "info@example.com"
    PASSWORDLESS_SIGN_IN: str = "true"
    # Fail closed: an unset ENV_SUFFIX must NOT grant local privileges (docs
    # exposure, local-bearer bypass). Local dev/tests set ENV_SUFFIX=local
    # explicitly (docker-compose, conftest).
    ENV_SUFFIX: str = os.getenv("ENV_SUFFIX", "production")
    LOCAL_BEARER_TOKEN: str = os.getenv("LOCAL_BEARER_TOKEN", "")
    LOCAL_BEARER_USER_EMAIL: str = os.getenv("LOCAL_BEARER_USER_EMAIL", "")
    DYNAMODB_REGION: str = "eu-west-1"
    SES_REGION: str = "eu-west-1"
    ENABLE_LIMITS: bool = os.getenv("ENABLE_LIMITS", "false") == "true"
    AI_TRIAL_MAX_BUDGET: float = os.getenv("AI_TRIAL_MAX_BUDGET", 2.0)
    # Hard ceiling on total trial users. The trial endpoint is unauthenticated,
    # so this bounds free-key farming / provisioning DoS regardless of request
    # rate. Per-IP throttling is expected at the ingress/edge.
    AI_TRIAL_MAX_USERS: int = int(os.getenv("AI_TRIAL_MAX_USERS", "1000"))
    AI_TRIAL_TEAM_EMAIL: str = os.getenv(
        "AI_TRIAL_TEAM_EMAIL", "anonymous-trial-user@example.com"
    )
    AI_TRIAL_REGION: str = os.getenv("AI_TRIAL_REGION", "eu-west-1")
    # Age at which an UNUSED trial key is reaped. Each trial provisions a
    # LiteLLM key AND a Postgres database, and nothing else ever reclaims
    # either, so without this they accumulate for the life of the region.
    #
    # Generous on purpose: someone can set up a site and not touch its AI key
    # for a month or two, so a short window would delete keys people are still
    # going to use.
    AI_TRIAL_RETENTION_DAYS: int = int(os.getenv("AI_TRIAL_RETENTION_DAYS", "90"))
    # Cap per reaper run. Each deletion is an HTTP call plus a DROP DATABASE,
    # so a first run against a large backlog is spread over several nights
    # rather than held open for hours.
    AI_TRIAL_REAP_BATCH_SIZE: int = int(os.getenv("AI_TRIAL_REAP_BATCH_SIZE", "500"))
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "sk_test_string")
    STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_string")
    HUBSPOT_TOKEN: str = os.getenv("HUBSPOT_TOKEN", "")
    HUBSPOT_MARKETING_UPDATES_PROPERTY: str = os.getenv(
        "HUBSPOT_MARKETING_UPDATES_PROPERTY", "receive_marketing_updates"
    )
    HUBSPOT_MARKETING_SUBSCRIPTION_ID: str | None = os.getenv(
        "HUBSPOT_MARKETING_SUBSCRIPTION_ID"
    )
    MOAD_DASHBOARD_API_URL: str = os.getenv("MOAD_DASHBOARD_API_URL", "")
    MOAD_DASHBOARD_API_TOKEN: str = os.getenv("MOAD_DASHBOARD_API_TOKEN", "")
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "false") == "true"

    # --- Trial-account abuse protection (moad #620) ---
    # Layer 1: disposable / dynamic-DNS email-domain blocking (defense-in-depth;
    # the backend must not trust callers to have filtered signup emails).
    ENABLE_DISPOSABLE_EMAIL_BLOCKING: bool = (
        os.getenv("ENABLE_DISPOSABLE_EMAIL_BLOCKING", "true") == "true"
    )
    DISPOSABLE_DOMAINS_URL: str = os.getenv(
        "DISPOSABLE_DOMAINS_URL",
        "https://disposable.github.io/disposable-email-domains/domains.txt",
    )
    # Extra domains merged with the committed baseline (comma-separated).
    # The blocklist itself lives in the disposable_domains table, repopulated by
    # the daily refresh cron (scripts/trigger_refresh_disposable_domains_job.py).
    DISPOSABLE_DOMAINS_EXTRA: str = os.getenv("DISPOSABLE_DOMAINS_EXTRA", "")

    # Layer 2: per-IP signup velocity cap (backed by the signup_events table).
    ENABLE_SIGNUP_VELOCITY_LIMIT: bool = (
        os.getenv("ENABLE_SIGNUP_VELOCITY_LIMIT", "true") == "true"
    )
    SIGNUP_MAX_PER_IP_PER_WINDOW: int = int(
        os.getenv("SIGNUP_MAX_PER_IP_PER_WINDOW", "5")
    )
    SIGNUP_VELOCITY_WINDOW_MINUTES: int = int(
        os.getenv("SIGNUP_VELOCITY_WINDOW_MINUTES", "60")
    )
    # Retention for the append-only signup_events table; rows older than this are
    # removed by the daily prune cron so the table can't grow without bound.
    SIGNUP_EVENTS_RETENTION_DAYS: int = int(
        os.getenv("SIGNUP_EVENTS_RETENTION_DAYS", "7")
    )
    # --- Budget threshold alerts (AI-448) ---
    # Off by default: enabling it starts sending webhooks to a third party, so it
    # must be an explicit per-environment decision.
    BUDGET_ALERT_ENABLED: bool = os.getenv("BUDGET_ALERT_ENABLED", "false") == "true"
    # Percentages of budget at which to notify, ascending, comma-separated.
    BUDGET_ALERT_THRESHOLDS: str = os.getenv("BUDGET_ALERT_THRESHOLDS", "50,75,90,100")
    # Full destination URL, not a base — the receiving path is MOAD's to choose,
    # so it is configured rather than hardcoded here.
    BUDGET_ALERT_WEBHOOK_URL: str = os.getenv("BUDGET_ALERT_WEBHOOK_URL", "")
    # Bearer token for the webhook. Falls back to the existing MOAD token so a
    # deployment that already talks to MOAD needs no extra secret.
    BUDGET_ALERT_WEBHOOK_TOKEN: str = os.getenv(
        "BUDGET_ALERT_WEBHOOK_TOKEN", os.getenv("MOAD_DASHBOARD_API_TOKEN", "")
    )
    BUDGET_ALERT_WEBHOOK_TIMEOUT: float = float(
        os.getenv("BUDGET_ALERT_WEBHOOK_TIMEOUT", "30")
    )
    BUDGET_ALERT_BATCH_SIZE: int = int(os.getenv("BUDGET_ALERT_BATCH_SIZE", "200"))
    BUDGET_ALERT_REGION_CONCURRENCY: int = int(
        os.getenv("BUDGET_ALERT_REGION_CONCURRENCY", "4")
    )
    # How far back the daily-activity sweep reaches. It must cover the oldest
    # still-valid ledger entry, because spend is counted from that entry's purchase
    # date — a shorter window understates the percentage. POOL entries live for
    # POOL_PURCHASE_EXPIRY_DAYS (365), so the default clears that with margin. The
    # cost is one extra day-row per day, all within a single page.
    BUDGET_ALERT_MAX_LOOKBACK_DAYS: int = int(
        os.getenv("BUDGET_ALERT_MAX_LOOKBACK_DAYS", "370")
    )
    # How long a budget *decrease* (an expired ledger entry, an edited spend cap)
    # keeps a team in the recheck set. A decrease raises the percentage without any
    # traffic, so such a team would otherwise never be looked at. Spans several
    # ticks deliberately: alert state prevents re-notification, so looking too
    # often is harmless where missing the one relevant tick is not.
    BUDGET_ALERT_RECHECK_GRACE_HOURS: int = int(
        os.getenv("BUDGET_ALERT_RECHECK_GRACE_HOURS", "24")
    )

    PROMETHEUS_API_KEY: str = os.getenv("PROMETHEUS_API_KEY", "")
    POOL_PURCHASE_EXPIRY_DAYS: int = int(os.getenv("POOL_PURCHASE_EXPIRY_DAYS", "365"))
    PERIODIC_TOPUP_EXPIRY_DAYS: int = int(
        os.getenv("PERIODIC_TOPUP_EXPIRY_DAYS", "365")
    )
    DEDICATED_DEFAULT_USER_COUNT: float | None = None
    DEDICATED_DEFAULT_SERVICE_KEYS: float | None = None
    DEDICATED_DEFAULT_VECTOR_DB_COUNT: float | None = None
    DEDICATED_DEFAULT_RPM_PER_KEY: float | None = None

    # URL of the upstream Amazon Bedrock model catalog used by /models/missing.
    # Defaults to the community-maintained mirror used by the k0rdent-clusters tooling.
    BEDROCK_MODELS_URL: str = os.getenv(
        "BEDROCK_MODELS_URL",
        "https://raw.githubusercontent.com/amazonbedrockmodels/amazonbedrockmodels.github.io/main/data/models.json",
    )
    # Per-region timeout for fetching bedrock model availability and our LiteLLM /model/info.
    BEDROCK_MISSING_MODELS_TIMEOUT_SECONDS: float = float(
        os.getenv("BEDROCK_MISSING_MODELS_TIMEOUT_SECONDS", "15")
    )

    # --- Model catalog (amazeeai-model-catalog) ---
    # Allow-list of region names the catalog may write to. Empty = catalog sync
    # is off entirely, which is the default so a deploy never starts pushing
    # models on its own. Private regions that keep their own LiteLLM config
    # elsewhere (ren2) must never be listed — their absence here is what stops
    # the catalog from overwriting or deleting their models.
    CATALOG_MANAGED_REGIONS: str = os.getenv("CATALOG_MANAGED_REGIONS", "")

    model_config = ConfigDict(env_file=".env", extra="ignore")
    main_route: str = os.getenv("LAGOON_ROUTE", "http://localhost:8800")
    frontend_route: str = os.getenv("FRONTEND_ROUTE", "http://localhost:3000")

    @field_validator("SECRET_KEY")
    @classmethod
    def reject_default_jwt_secret(cls, value):
        if (
            not value
            or value in ("my-secret-key", "test-secret-key")
            or "CHANGE_ME" in value
        ):
            raise ValueError(
                "AMAZEEAI_JWT_SECRET must be set to a strong, non-default value."
            )
        # Reject obviously-weak short secrets (e.g. "secret", "changeme").
        # Generate one with: openssl rand -hex 32
        if len(value) < 32:
            raise ValueError(
                "AMAZEEAI_JWT_SECRET must be at least 32 characters "
                "(e.g. `openssl rand -hex 32`)."
            )
        return value

    @field_validator(
        "DEDICATED_DEFAULT_USER_COUNT",
        "DEDICATED_DEFAULT_SERVICE_KEYS",
        "DEDICATED_DEFAULT_VECTOR_DB_COUNT",
        "DEDICATED_DEFAULT_RPM_PER_KEY",
        mode="before",
    )
    @classmethod
    def validate_optional_dedicated_float(cls, value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Dedicated default limit values must be numeric when set."
            ) from exc

    def model_post_init(self, values):
        # Add Lagoon routes to CORS origins if available
        lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
        self.CORS_ORIGINS.extend(
            [route.strip() for route in lagoon_routes if route.strip()]
        )


settings = Settings()


def catalog_manages(region_name: str) -> bool:
    """True if the model catalog may push to this region's LiteLLM proxy.

    Read at call time, not import time, so tests and a Lagoon env-var change
    take effect without a code change.
    """
    # ponytail: reuse the existing local-privileges signal instead of a "*"
    # wildcard — docker-compose and the test suite already set ENV_SUFFIX=local,
    # and prod ("production") can never reach this branch, so there is no
    # single value an operator can set that opens every region on prod.
    if settings.ENV_SUFFIX == "local":
        return True
    return region_name in {
        r.strip() for r in settings.CATALOG_MANAGED_REGIONS.split(",") if r.strip()
    }
