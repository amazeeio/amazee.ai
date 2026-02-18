from pydantic_settings import BaseSettings
from pydantic import ConfigDict
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
        "http://localhost:8800"
    ]
    ALLOWED_HOSTS: list[str] = ["*"]  # In production, restrict this
    PUBLIC_PATHS: list[str] = ["/health", "/docs", "/openapi.json"]

    AWS_ACCESS_KEY_ID: str = "AKIATEST"
    AWS_SECRET_ACCESS_KEY: str = "sk-string"
    SES_SENDER_EMAIL: str = "info@example.com"
    PASSWORDLESS_SIGN_IN: str = "true"
    ENV_SUFFIX: str = os.getenv("ENV_SUFFIX", "local")
    DYNAMODB_REGION: str = "eu-west-1"
    SES_REGION: str = "eu-west-1"
    ENABLE_LIMITS: bool = os.getenv("ENABLE_LIMITS", "false") == "true"
    AI_TRIAL_MAX_BUDGET: float = os.getenv("AI_TRIAL_MAX_BUDGET", 2.0)
    AI_TRIAL_TEAM_EMAIL: str = os.getenv("AI_TRIAL_TEAM_EMAIL", "anonymous-trial-user@example.com")
    AI_TRIAL_REGION: str = os.getenv("AI_TRIAL_REGION", "eu-west-1")
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "sk_test_string")
    STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "pk_test_string")
    WEBHOOK_SIG: str = os.getenv("WEBHOOK_SIG", "whsec_test_1234567890")
    ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "false") == "true"
    PROMETHEUS_API_KEY: str = os.getenv("PROMETHEUS_API_KEY", "")

    model_config = ConfigDict(env_file=".env", extra="ignore")
    main_route: str = os.getenv("LAGOON_ROUTE", "http://localhost:8800")
    frontend_route: str = os.getenv("FRONTEND_ROUTE", "http://localhost:3000")

    def model_post_init(self, values):
        # Add Lagoon routes to CORS origins if available
        lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
        self.CORS_ORIGINS.extend([route.strip() for route in lagoon_routes if route.strip()])

settings = Settings()
