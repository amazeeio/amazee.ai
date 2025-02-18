from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import os

from app.api import auth, private_ai_keys, users, tokens, regions
from app.core.config import settings
from app.db.database import get_db

app = FastAPI(
    title="Postgres as a Service",
    root_path="",
    root_path_in_servers=True,
    server_options={"forwarded_allow_ips": "*"}
)

# Get allowed origins from environment
default_origins = ["http://localhost:8080", "http://localhost:3000", "http://localhost:8800"]
lagoon_routes = os.getenv("LAGOON_ROUTES", "").split(",")
allowed_origins = default_origins + [route.strip() for route in lagoon_routes if route.strip()]

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add trusted host middleware
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # In production, you might want to restrict this
)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(private_ai_keys.router, prefix="/private-ai-keys", tags=["private-ai-keys"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(tokens.router, prefix="/tokens", tags=["tokens"])
app.include_router(regions.router, prefix="/regions", tags=["regions"])

@app.get("/")
def read_root():
    return {"message": "Welcome to Private AI Key Service"}