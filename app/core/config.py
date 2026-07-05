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
        # /openapi.json is only registered in local (see main.py openapi_url);
        # it must stay public so the local Swagger UI can fetch the schema. In
        # deployed envs the route does not exist, so this entry is inert.
        # (/docs is never registered — docs_url=None — so it is not listed.)
        "/openapi.json",
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
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "sk_test_string")
    STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_string")
    WEBHOOK_SIG: str = os.getenv("WEBHOOK_SIG", "whsec_test_1234567890")
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

    model_config = ConfigDict(env_file=".env", extra="ignore")
    main_route: str = os.getenv("LAGOON_ROUTE", "http://localhost:8800")
    frontend_route: str = os.getenv("FRONTEND_ROUTE", "http://localhost:3000")

    @field_validator("SECRET_KEY")
    @classmethod
    def reject_default_jwt_secret(cls, value):
        if not value or value in ("my-secret-key", "test-secret-key"):
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
