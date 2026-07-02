from pydantic_settings import BaseSettings
from pydantic import ConfigDict, field_validator
import os


class Settings(BaseSettings):
    # Database settings
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres/postgres_service"
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "50"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "50"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

    # JWT settings
    SECRET_KEY: str = os.environ["AMAZEEAI_JWT_SECRET"]
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
    PUBLIC_PATHS: list[str] = [
        "/health",
        "/docs",
        "/openapi.json",
        "/public/models",
        "/public/models/",
    ]

    AWS_ACCESS_KEY_ID: str | None = None
    AWS_SECRET_ACCESS_KEY: str | None = None
    SES_SENDER_EMAIL: str = "info@example.com"
    PASSWORDLESS_SIGN_IN: str = "true"
    ENV_SUFFIX: str = os.getenv("ENV_SUFFIX", "local")
    LOCAL_BEARER_TOKEN: str = os.getenv("LOCAL_BEARER_TOKEN", "")
    LOCAL_BEARER_USER_EMAIL: str = os.getenv("LOCAL_BEARER_USER_EMAIL", "")
    DYNAMODB_REGION: str = "eu-west-1"
    SES_REGION: str = "eu-west-1"
    ENABLE_LIMITS: bool = os.getenv("ENABLE_LIMITS", "false") == "true"
    AI_TRIAL_MAX_BUDGET: float = os.getenv("AI_TRIAL_MAX_BUDGET", 2.0)
    AI_TRIAL_TEAM_EMAIL: str = os.getenv(
        "AI_TRIAL_TEAM_EMAIL", "anonymous-trial-user@example.com"
    )
    AI_TRIAL_REGION: str = os.getenv("AI_TRIAL_REGION", "eu-west-1")
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None
    WEBHOOK_SIG: str | None = None
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

        # Set development/testing fallbacks for required variables
        if self.ENV_SUFFIX in ("local", "test", "testing"):
            if not self.AWS_ACCESS_KEY_ID:
                self.AWS_ACCESS_KEY_ID = "AKIATEST"
            if not self.AWS_SECRET_ACCESS_KEY:
                self.AWS_SECRET_ACCESS_KEY = "sk-string"
            if not self.STRIPE_SECRET_KEY:
                self.STRIPE_SECRET_KEY = "sk_test_string"
            if not self.STRIPE_PUBLISHABLE_KEY:
                self.STRIPE_PUBLISHABLE_KEY = "pk_test_string"
            if not self.WEBHOOK_SIG:
                self.WEBHOOK_SIG = "whsec_test_1234567890"

        # Validate required production variables
        if self.ENV_SUFFIX not in ("local", "test", "testing"):
            missing = []
            if not self.AWS_ACCESS_KEY_ID:
                missing.append("AWS_ACCESS_KEY_ID")
            if not self.AWS_SECRET_ACCESS_KEY:
                missing.append("AWS_SECRET_ACCESS_KEY")
            if not self.STRIPE_SECRET_KEY:
                missing.append("STRIPE_SECRET_KEY")
            if not self.STRIPE_PUBLISHABLE_KEY:
                missing.append("STRIPE_PUBLISHABLE_KEY")
            if not self.WEBHOOK_SIG:
                missing.append("WEBHOOK_SIG")
            if missing:
                raise ValueError(
                    f"Missing required production environment variables: {', '.join(missing)}"
                )


settings = Settings()
