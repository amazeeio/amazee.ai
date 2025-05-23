from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBUser, DBPrivateAIKey
from fastapi import HTTPException, status
from typing import Optional
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)

# Default limits across all customers and products
DEFAULT_USER_COUNT = 1
DEFAULT_KEYS_PER_USER = 1
DEFAULT_TOTAL_KEYS = 2
DEFAULT_SERVICE_KEYS = 1
DEFAULT_VECTOR_DB_COUNT = 1
DEFAULT_KEY_DURATION = 30
DEFAULT_MAX_SPEND = 20.0
DEFAULT_RPM_PER_KEY = 500

def check_team_user_limit(db: Session, team_id: int) -> None:
    """
    Check if adding a user would exceed the team's product limits.
    Raises HTTPException if the limit would be exceeded.

    Args:
        db: Database session
        team_id: ID of the team to check
    """
    # Get current user count for the team
    current_user_count = db.query(DBUser).filter(DBUser.team_id == team_id).count()

    # Get all active products for the team
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Find the maximum user count allowed across all active products
    max_user_count = max(
        (product.user_count for team_product in team.active_products
         for product in [team_product.product] if product.user_count),
        default=DEFAULT_USER_COUNT  # Default to 2 if no products have user_count set
    )

    if current_user_count >= max_user_count:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum user limit of {max_user_count} users"
        )

def check_key_limits(db: Session, team_id: int, owner_id: Optional[int] = None) -> None:
    """
    Check if creating a new LLM token would exceed the team's or user's key limits.
    Raises HTTPException if any limit would be exceeded.

    Args:
        db: Database session
        team_id: ID of the team to check
        owner_id: Optional ID of the user who will own the key
    """
    # Get the team and its active products
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Find the maximum limits across all active products, using defaults if no products
    max_total_keys = max(
        (product.total_key_count for team_product in team.active_products
         for product in [team_product.product] if product.total_key_count),
        default=DEFAULT_TOTAL_KEYS  # Default to 2 if no products have total_key_count set
    )
    max_keys_per_user = max(
        (product.keys_per_user for team_product in team.active_products
         for product in [team_product.product] if product.keys_per_user),
        default=DEFAULT_KEYS_PER_USER  # Default to 1 if no products have keys_per_user set
    )
    max_service_keys = max(
        (product.service_key_count for team_product in team.active_products
         for product in [team_product.product] if product.service_key_count),
        default=DEFAULT_SERVICE_KEYS  # Default to 1 if no products have service_key_count set
    )

    # Get all users in the team
    team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
    user_ids = [user.id for user in team_users]

    # Check total team LLM tokens (both team-owned and user-owned)
    current_team_tokens = db.query(DBPrivateAIKey).filter(
        (
            (DBPrivateAIKey.team_id == team_id) |  # Team-owned tokens
            (DBPrivateAIKey.owner_id.in_(user_ids))  # User-owned tokens
        ),
        DBPrivateAIKey.litellm_token.isnot(None)  # Only count LLM tokens
    ).count()
    if current_team_tokens >= max_total_keys:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum LLM token limit of {max_total_keys} tokens"
        )

    # Check user LLM tokens if owner_id is provided
    if owner_id is not None:
        current_user_tokens = db.query(DBPrivateAIKey).filter(
            DBPrivateAIKey.owner_id == owner_id,
            DBPrivateAIKey.litellm_token.isnot(None)  # Only count LLM tokens
        ).count()
        if current_user_tokens >= max_keys_per_user:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"User has reached the maximum LLM token limit of {max_keys_per_user} tokens"
            )

    # Check service LLM tokens (team-owned tokens)
    if owner_id is None:  # This is a team-owned token
        current_service_tokens = db.query(DBPrivateAIKey).filter(
            DBPrivateAIKey.team_id == team_id,
            DBPrivateAIKey.owner_id.is_(None),
            DBPrivateAIKey.litellm_token.isnot(None)  # Only count LLM tokens
        ).count()
        if current_service_tokens >= max_service_keys:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Team has reached the maximum service LLM token limit of {max_service_keys} tokens"
            )

def check_vector_db_limits(db: Session, team_id: int) -> None:
    """
    Check if creating a new vector DB would exceed the team's vector DB limits.
    Raises HTTPException if the limit would be exceeded.

    Args:
        db: Database session
        team_id: ID of the team to check
    """
    # Get the team and its active products
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Find the maximum vector DB count across all active products
    max_vector_db_count = max(
        (product.vector_db_count for team_product in team.active_products
         for product in [team_product.product] if product.vector_db_count),
        default=DEFAULT_VECTOR_DB_COUNT  # Default to 1 if no products have vector_db_count set
    )

    # Get all users in the team
    team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
    user_ids = [user.id for user in team_users]

    # Get current vector DB count for the team (both team-owned and user-owned)
    current_vector_db_count = db.query(DBPrivateAIKey).filter(
        (
            (DBPrivateAIKey.team_id == team_id) |  # Team-owned vector DBs
            (DBPrivateAIKey.owner_id.in_(user_ids))  # User-owned vector DBs
        ),
        DBPrivateAIKey.database_name.isnot(None)  # Only count keys with database_name set
    ).count()

    if current_vector_db_count >= max_vector_db_count:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum vector DB limit of {max_vector_db_count} databases"
        )

def get_token_restrictions(db: Session, team_id: int) -> tuple[int, float, int]:
    """
    Get the token restrictions for a team.
    """
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not team:
        logger.error(f"Team not found for team_id: {team_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    max_key_duration = max(
        (product.renewal_period_days for team_product in team.active_products
         for product in [team_product.product] if product.renewal_period_days),
        default=DEFAULT_KEY_DURATION
    )
    if team.last_payment is None:
        days_left_in_period = max_key_duration
    else:
        days_left_in_period = max_key_duration - (datetime.now(UTC) - max(team.created_at, team.last_payment)).days
    max_max_spend = max(
        (product.max_budget_per_key for team_product in team.active_products
         for product in [team_product.product] if product.max_budget_per_key),
        default=DEFAULT_MAX_SPEND
    )
    max_rpm_limit = max(
        (product.rpm_per_key for team_product in team.active_products
         for product in [team_product.product] if product.rpm_per_key),
        default=DEFAULT_RPM_PER_KEY
    )

    return days_left_in_period, max_max_spend, max_rpm_limit