from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBUser, DBProduct, DBPrivateAIKey
from fastapi import HTTPException, status
from typing import Optional

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
        raise HTTPException(status_code=404, detail="Team not found")

    # Find the maximum user count allowed across all active products
    max_user_count = max(
        (product.user_count for team_product in team.active_products
         for product in [team_product.product] if product.user_count),
        default=2  # Default to 2 if no products have user_count set
    )

    if current_user_count >= max_user_count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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
        raise HTTPException(status_code=404, detail="Team not found")

    # Find the maximum limits across all active products, using defaults if no products
    max_total_keys = max(
        (product.total_key_count for team_product in team.active_products
         for product in [team_product.product] if product.total_key_count),
        default=2  # Default to 2 if no products have total_key_count set
    )
    max_keys_per_user = max(
        (product.keys_per_user for team_product in team.active_products
         for product in [team_product.product] if product.keys_per_user),
        default=1  # Default to 1 if no products have keys_per_user set
    )
    max_service_keys = max(
        (product.service_key_count for team_product in team.active_products
         for product in [team_product.product] if product.service_key_count),
        default=1  # Default to 1 if no products have service_key_count set
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
            status_code=status.HTTP_400_BAD_REQUEST,
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
                status_code=status.HTTP_400_BAD_REQUEST,
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
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Team has reached the maximum service LLM token limit of {max_service_keys} tokens"
            )
