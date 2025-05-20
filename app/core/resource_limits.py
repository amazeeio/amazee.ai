from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBUser, DBProduct
from fastapi import HTTPException, status

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

    # If team has no products, use default limit of 2
    if not team.active_products:
        if current_user_count >= 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team has reached the default user limit of 2 users"
            )
        return

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
