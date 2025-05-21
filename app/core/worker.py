from datetime import datetime, UTC
from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBProduct, DBTeamProduct, DBPrivateAIKey, DBUser
from app.services.litellm import LiteLLMService
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

async def apply_product_for_team(db: Session, customer_id: str, product_id: str):
    """
    Apply a product to a team and update their last payment date.
    Also extends all team keys and sets their max budgets via LiteLLM service.

    Args:
        db: Database session
        customer_id: Stripe customer ID
        product_id: Product ID from the database

    Returns:
        bool: True if update was successful, False otherwise
    """
    logger.info(f"Applying product {product_id} to team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return

        # Update the last payment date
        team.last_payment = datetime.now(UTC)

        # Check if the product is already active for the team
        existing_association = db.query(DBTeamProduct).filter(
            DBTeamProduct.team_id == team.id,
            DBTeamProduct.product_id == product.id
        ).first()

        # Only create new association if it doesn't exist
        if not existing_association:
            team_product = DBTeamProduct(
                team_id=team.id,
                product_id=product.id
            )
            db.add(team_product)

        # Get all keys for the team with their regions
        team_users = db.query(DBUser).filter(DBUser.team_id == team.id).all()
        team_user_ids = [user.id for user in team_users]
        # Return keys owned by users in the team OR owned by the team
        team_keys = db.query(DBPrivateAIKey).filter(
            (DBPrivateAIKey.owner_id.in_(team_user_ids)) |
            (DBPrivateAIKey.team_id == team.id)
        ).all()

        # Group keys by region
        keys_by_region = defaultdict(list)
        for key in team_keys:
            if not key.litellm_token:
                logger.warning(f"Key {key.id} has no LiteLLM token, skipping")
                continue
            if not key.region:
                logger.warning(f"Key {key.id} has no region, skipping")
                continue
            keys_by_region[key.region].append(key)

        # Update keys for each region
        for region, keys in keys_by_region.items():
            # Initialize LiteLLM service for this region
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )

            # Update each key's duration and budget via LiteLLM
            for key in keys:
                try:
                    # Update key duration
                    await litellm_service.update_key_duration(
                        litellm_token=key.litellm_token,
                        duration=f"{product.renewal_period_days}d"
                    )

                    # Update key budget
                    await litellm_service.update_budget(
                        litellm_token=key.litellm_token,
                        budget_duration=f"{product.renewal_period_days}d",
                        budget_amount=product.max_budget_per_key
                    )
                except Exception as e:
                    logger.error(f"Failed to update key {key.id} via LiteLLM: {str(e)}")
                    # Continue with other keys even if one fails
                    continue

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Error applying product to team: {str(e)}")
        raise e

async def remove_product_from_team(db: Session, customer_id: str, product_id: str):
    logger.info(f"Removing product {product_id} from team {customer_id}")
    try:
        # Find the team and product
        team = db.query(DBTeam).filter(DBTeam.stripe_customer_id == customer_id).first()
        product = db.query(DBProduct).filter(DBProduct.id == product_id).first()

        if not team:
            logger.error(f"Team not found for customer ID: {customer_id}")
            return
        if not product:
            logger.error(f"Product not found for ID: {product_id}")
            return
        # Check if the product is already active for the team
        existing_association = db.query(DBTeamProduct).filter(
            DBTeamProduct.team_id == team.id,
            DBTeamProduct.product_id == product.id
        ).first()
        if not existing_association:
            logger.error(f"Product {product_id} not found for team {customer_id}")
            return
        # Remove the product association
        db.delete(existing_association)

    # TODO: Send notification
    # TODO: Expire keys if applicable
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing product from team: {str(e)}")
        raise e
