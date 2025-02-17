from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    # Database settings
    DATABASE_URL: str = "postgresql://postgres:postgres@postgres/postgres_service"

    # JWT settings
    SECRET_KEY: str = "your-secret-key-here"  # In production, use environment variable
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # Increase to 60 minutes

    model_config = ConfigDict(env_file=".env")

settings = Settings()