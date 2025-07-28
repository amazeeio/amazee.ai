from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBTeamProduct, DBProduct
from fastapi import HTTPException, status
from typing import Optional
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)

# Default limits across all customers and products
DEFAULT_USER_COUNT = 100
DEFAULT_KEYS_PER_USER = 5
DEFAULT_TOTAL_KEYS = 500
DEFAULT_SERVICE_KEYS = 100
DEFAULT_VECTOR_DB_COUNT = 100
DEFAULT_KEY_DURATION = 30
DEFAULT_MAX_SPEND = 27.0
DEFAULT_RPM_PER_KEY = 500

def check_team_user_limit(db: Session, team_id: int) -> None:
    """
    Check if adding a user would exceed the team's product limits.
    Raises HTTPException if the limit would be exceeded.

    Args:
        db: Database session
        team_id: ID of the team to check
    """
    # Get current user count and max allowed users in a single query
    result = db.query(
        func.count(DBUser.id).label('current_user_count'),
        func.coalesce(func.max(DBProduct.user_count), DEFAULT_USER_COUNT).label('max_users')
    ).select_from(DBUser).filter(
        DBUser.team_id == team_id
    ).outerjoin(
        DBTeamProduct,
        DBTeamProduct.team_id == team_id
    ).outerjoin(
        DBProduct,
        DBProduct.id == DBTeamProduct.product_id
    ).first()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if result.current_user_count >= result.max_users:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum user limit of {result.max_users} users"
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
    # Get all limits and current counts in a single query
    result = db.query(
        func.coalesce(func.max(DBProduct.total_key_count), DEFAULT_TOTAL_KEYS).label('max_total_keys'),
        func.coalesce(func.max(DBProduct.keys_per_user), DEFAULT_KEYS_PER_USER).label('max_keys_per_user'),
        func.coalesce(func.max(DBProduct.service_key_count), DEFAULT_SERVICE_KEYS).label('max_service_keys'),
        func.count(DBPrivateAIKey.id).filter(
            DBPrivateAIKey.litellm_token.isnot(None)
        ).label('current_team_keys'),
        func.count(DBPrivateAIKey.id).filter(
            DBPrivateAIKey.owner_id == owner_id,
            DBPrivateAIKey.litellm_token.isnot(None)
        ).label('current_user_keys') if owner_id else None,
        func.count(DBPrivateAIKey.id).filter(
            DBPrivateAIKey.owner_id.is_(None),
            DBPrivateAIKey.litellm_token.isnot(None)
        ).label('current_service_keys')
    ).select_from(DBTeam).filter( # Have to use Teams table as the base because not every team has a product
        DBTeam.id == team_id
    ).outerjoin(
        DBTeamProduct,
        DBTeamProduct.team_id == DBTeam.id
    ).outerjoin(
        DBProduct,
        DBProduct.id == DBTeamProduct.product_id
    ).outerjoin(
        DBPrivateAIKey,
        or_(
            DBPrivateAIKey.team_id == DBTeam.id,
            DBPrivateAIKey.owner_id.in_(
                db.query(DBUser.id).filter(DBUser.team_id == DBTeam.id)
            )
        )
    ).first()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if result.current_team_keys >= result.max_total_keys:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum LLM key limit of {result.max_total_keys} keys"
        )

    if owner_id is not None and result.current_user_keys >= result.max_keys_per_user:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"User has reached the maximum LLM key limit of {result.max_keys_per_user} keys"
        )

    if owner_id is None and result.current_service_keys >= result.max_service_keys:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum service LLM key limit of {result.max_service_keys} keys"
        )

def check_vector_db_limits(db: Session, team_id: int) -> None:
    """
    Check if creating a new vector DB would exceed the team's vector DB limits.
    Raises HTTPException if the limit would be exceeded.

    Args:
        db: Database session
        team_id: ID of the team to check
    """
    # Get vector DB limits and current count in a single query
    result = db.query(
        func.coalesce(func.max(DBProduct.vector_db_count), DEFAULT_VECTOR_DB_COUNT).label('max_vector_db_count'),
        func.count(DBPrivateAIKey.id).filter(
            DBPrivateAIKey.database_name.isnot(None)
        ).label('current_vector_db_count')
    ).select_from(DBTeam).filter(
        DBTeam.id == team_id
    ).outerjoin(
        DBTeamProduct,
        DBTeamProduct.team_id == DBTeam.id
    ).outerjoin(
        DBProduct,
        DBProduct.id == DBTeamProduct.product_id
    ).outerjoin(
        DBPrivateAIKey,
        or_(
            DBPrivateAIKey.team_id == DBTeam.id,
            DBPrivateAIKey.owner_id.in_(
                db.query(DBUser.id).filter(DBUser.team_id == DBTeam.id)
            )
        )
    ).first()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if result.current_vector_db_count >= result.max_vector_db_count:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum vector DB limit of {result.max_vector_db_count} databases"
        )

def get_token_restrictions(db: Session, team_id: int) -> tuple[int, float, int]:
    """
    Get the token restrictions for a team.
    """
    # Get all token restrictions in a single query
    result = db.query(
        func.coalesce(func.max(DBProduct.renewal_period_days), DEFAULT_KEY_DURATION).label('max_key_duration'),
        func.coalesce(func.max(DBProduct.max_budget_per_key), DEFAULT_MAX_SPEND).label('max_max_spend'),
        func.coalesce(func.max(DBProduct.rpm_per_key), DEFAULT_RPM_PER_KEY).label('max_rpm_limit'),
        DBTeam.created_at,
        DBTeam.last_payment
    ).select_from(DBTeam).filter(
        DBTeam.id == team_id
    ).outerjoin(
        DBTeamProduct,
        DBTeamProduct.team_id == DBTeam.id
    ).outerjoin(
        DBProduct,
        DBProduct.id == DBTeamProduct.product_id
    ).group_by(
        DBTeam.created_at,
        DBTeam.last_payment
    ).first()

    if not result:
        logger.error(f"Team not found for team_id: {team_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if result.last_payment is None:
        days_left_in_period = result.max_key_duration
    else:
        days_left_in_period = result.max_key_duration - (
            datetime.now(UTC) - max(result.created_at.replace(tzinfo=UTC), result.last_payment.replace(tzinfo=UTC))
        ).days

    return days_left_in_period, result.max_max_spend, result.max_rpm_limit

def get_team_limits(db: Session, team_id: int):
    # TODO: Go through all products, and create a master list of the limits on all fields for this team.
    pass