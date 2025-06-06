from pydantic_settings import BaseSettings
from pydantic import ConfigDict
import os

class Settings(BaseSettings):
    # Database settings
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres/postgres_service"

    # JWT settings
    SECRET_KEY: str = "your-secret-key-here"  # In production, use environment variable
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
    PUBLIC_PATHS: list[str] = ["/health", "/docs", "/openapi.json", "/metrics"]

    AWS_ACCESS_KEY_ID: str = "AKIATEST"
    AWS_SECRET_ACCESS_KEY: str = "sk-string"
    SES_SENDER_EMAIL: str = "info@example.com"
    PASSWORDLESS_SIGN_IN: str = "true"
    ENV_SUFFIX: str = os.getenv("ENV_SUFFIX", "local")
    DYNAMODB_REGION: str = "eu-west-1"
    SES_REGION: str = "eu-west-1"
    ENABLE_LIMITS: bool = os.getenv("ENABLE_LIMITS", "false") == "true"
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "sk_test_string")
    WEBHOOK_SIG: str = os.getenv("WEBHOOK_SIG", "whsec_test_1234567890")

    model_config = ConfigDict(env_file=".env")
    main_route: str = os.getenv("LAGOON_ROUTE", "http://localhost:8800")

    def model_post_init(self, values):
        # Add Lagoon routes to CORS origins if available
        lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
        self.CORS_ORIGINS.extend([route.strip() for route in lagoon_routes if route.strip()])

settings = Settings()
