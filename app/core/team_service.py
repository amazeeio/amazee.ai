"""
Team service for centralized team operations including soft-delete and restore.
"""
import logging
from datetime import datetime, UTC
from typing import Dict, List
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBRegion
from app.services.litellm import LiteLLMService
from app.core.limit_service import DEFAULT_KEY_DURATION


logger = logging.getLogger(__name__)


def get_team_keys_by_region(db: Session, team_id: int) -> Dict[DBRegion, List[DBPrivateAIKey]]:
    """
    Get all keys for a team grouped by region.

    Args:
        db: Database session
        team_id: ID of the team to get keys for

    Returns:
        Dictionary mapping regions to lists of keys
    """
    # Get all keys for the team with their regions
    team_user_ids = db.execute(select(DBUser.id).filter(DBUser.team_id == team_id)).scalars().all()
    # Return keys owned by users in the team OR owned by the team
    team_keys = db.query(DBPrivateAIKey).filter(
        (DBPrivateAIKey.owner_id.in_(team_user_ids)) |
        (DBPrivateAIKey.team_id == team_id)
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

    logger.info(f"Found {len(team_keys)} keys in {len(keys_by_region)} regions for team {team_id}")
    return keys_by_region


async def soft_delete_team(db: Session, team: DBTeam, current_time: datetime = None) -> None:
    """
    Soft delete a team with full cascade behavior.

    This function:
    - Sets team.deleted_at timestamp
    - Deactivates all users in the team (is_active = False)
    - Expires all keys in LiteLLM (duration = 0s)

    Args:
        db: Database session
        team: The team to soft delete
        current_time: Optional timestamp to use (defaults to now)

    Note: This function commits the database changes.
    """
    if current_time is None:
        current_time = datetime.now(UTC)

    logger.info(f"Soft deleting team {team.id} ({team.name})")

    # Set deleted_at timestamp and deactivate team
    team.deleted_at = current_time
    team.is_active = False

    # Deactivate all users in the team
    users_deactivated = db.query(DBUser).filter(DBUser.team_id == team.id).update(
        {"is_active": False},
        synchronize_session=False
    )
    logger.info(f"Deactivated {users_deactivated} users for soft-deleted team {team.id}")

    # Commit the deletion and user deactivation
    db.commit()

    # Expire all keys in LiteLLM
    try:
        keys_by_region = get_team_keys_by_region(db, team.id)
        for region, keys in keys_by_region.items():
            try:
                litellm_service = LiteLLMService(
                    api_url=region.litellm_api_url,
                    api_key=region.litellm_api_key
                )
                for key in keys:
                    if key.litellm_token:
                        try:
                            await litellm_service.update_key_duration(
                                litellm_token=key.litellm_token,
                                duration="0d"
                            )
                            logger.info(f"Expired key {key.id} in LiteLLM for soft-deleted team {team.id}")
                        except Exception as key_error:
                            logger.error(f"Failed to expire key {key.id} in LiteLLM: {str(key_error)}")
            except Exception as region_error:
                logger.error(f"Failed to expire keys in region {region.name}: {str(region_error)}")
    except Exception as expire_error:
        logger.error(f"Failed to expire keys for team {team.id}: {str(expire_error)}")
        # Don't fail soft-deletion if key expiration fails

    logger.info(f"Successfully soft-deleted team {team.id} ({team.name})")


async def restore_soft_deleted_team(db: Session, team: DBTeam) -> None:
    """
    Restore a soft-deleted team with full cascade behavior.

    This function:
    - Resets team.deleted_at to null
    - Resets team.retention_warning_sent_at to null
    - Reactivates all users in the team (is_active = True)
    - Un-expires all keys in LiteLLM (sets back to default duration)

    Args:
        db: Database session
        team: The team to restore

    Note: This function commits the database changes.
    """
    if not team.deleted_at:
        raise ValueError(f"Team {team.id} is not soft-deleted and cannot be restored")

    logger.info(f"Restoring soft-deleted team {team.id} ({team.name})")

    # Un-expire all keys for the team in LiteLLM
    try:
        keys_by_region = get_team_keys_by_region(db, team.id)
        for region, keys in keys_by_region.items():
            try:
                litellm_service = LiteLLMService(
                    api_url=region.litellm_api_url,
                    api_key=region.litellm_api_key
                )
                for key in keys:
                    if key.litellm_token:
                        try:
                            await litellm_service.update_key_duration(
                                litellm_token=key.litellm_token,
                                duration=f"{DEFAULT_KEY_DURATION}d"
                            )
                            logger.info(f"Un-expired key {key.id} in LiteLLM for restored team {team.id}")
                        except Exception as key_error:
                            logger.error(f"Failed to un-expire key {key.id} in LiteLLM: {str(key_error)}")
            except Exception as region_error:
                logger.error(f"Failed to un-expire keys in region {region.name}: {str(region_error)}")
    except Exception as restore_error:
        logger.error(f"Failed to un-expire keys for team {team.id}: {str(restore_error)}")
        # Don't block restoration if key un-expiration fails

    # Reset deletion and warning timestamps and reactivate team
    team.deleted_at = None
    team.retention_warning_sent_at = None
    team.is_active = True
    team.updated_at = datetime.now(UTC)

    # Reactivate all users in the team
    users_reactivated = db.query(DBUser).filter(DBUser.team_id == team.id).update(
        {"is_active": True},
        synchronize_session=False
    )
    logger.info(f"Reactivated {users_reactivated} users for restored team {team.id}")

    db.commit()

    logger.info(f"Successfully restored team {team.id} ({team.name})")


async def propagate_team_budget_to_keys(db: Session, team_id: int, budget_amount: float, budget_duration: str) -> None:
    """
    Propagate a team budget limit change to all keys belonging to the team.

    This function updates all keys (both user-owned and team-owned) in LiteLLM
    with the new budget amount when a team's budget limit is changed.

    Args:
        db: Database session
        team_id: ID of the team whose keys should be updated
        budget_amount: New budget amount to set for all keys
        budget_duration: Budget duration string (e.g., "30d")

    Note:
        Errors during key updates are logged but don't raise exceptions.
        This ensures that limit updates succeed even if some key updates fail.
    """
    try:
        keys_by_region = get_team_keys_by_region(db, team_id)

        # Update keys for each region
        for region, keys in keys_by_region.items():
            litellm_service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )

            # Update each key's budget via LiteLLM
            for key in keys:
                try:
                    # Update budget using the provided budget_duration
                    await litellm_service.update_budget(
                        litellm_token=key.litellm_token,
                        budget_duration=budget_duration,
                        budget_amount=budget_amount
                    )
                    logger.info(f"Updated key {key.id} budget to {budget_amount} in LiteLLM after team budget limit change")
                except Exception as key_error:
                    logger.error(f"Failed to update key {key.id} budget in LiteLLM: {str(key_error)}")
                    # Continue with other keys even if one fails
                    continue
    except Exception as propagation_error:
        logger.error(f"Error propagating budget limit to keys for team {team_id}: {str(propagation_error)}")
        # Don't raise - allow limit update to succeed even if propagation fails

