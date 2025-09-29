"""
FastAPI dependency functions for core services.

This module provides dependency injection functions for various services
used throughout the application.
"""

from fastapi import Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.limit_service import LimitService


def get_limit_service(db: Session = Depends(get_db)):
    """
    FastAPI dependency to provide LimitService instance.

    Args:
        db: Database session dependency

    Returns:
        LimitService: Configured limit service instance
    """
    return LimitService(db)
