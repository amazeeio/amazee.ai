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

    model_config = ConfigDict(env_file=".env")

    def model_post_init(self, values):
        # Add Lagoon routes to CORS origins if available
        lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
        self.CORS_ORIGINS.extend([route.strip() for route in lagoon_routes if route.strip()])

settings = Settings()