"""
Team service for centralized team operations including soft-delete and restore.
"""

import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Dict, List, Optional

from app.core.limit_service import DEFAULT_KEY_DURATION
from app.db.models import DBPrivateAIKey, DBRegion, DBTeam, DBUser
from app.services.litellm import LiteLLMService
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_team_region_litellm_keys(
    db: Session,
    *,
    team_id: int,
    region_id: int,
    key_id: int | None = None,
    user_id: int | None = None,
) -> List[DBPrivateAIKey]:
    """Return team keys in a region that have LiteLLM tokens."""
    team_user_ids = (
        db.execute(select(DBUser.id).filter(DBUser.team_id == team_id)).scalars().all()
    )
    ownership_filter = DBPrivateAIKey.team_id == team_id
    if team_user_ids:
        ownership_filter = or_(
            DBPrivateAIKey.team_id == team_id,
            DBPrivateAIKey.owner_id.in_(team_user_ids),
        )
    query = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.region_id == region_id,
        DBPrivateAIKey.litellm_token.isnot(None),
        ownership_filter,
    )
    if key_id is not None:
        query = query.filter(DBPrivateAIKey.id == key_id)
    if user_id is not None:
        query = query.filter(DBPrivateAIKey.owner_id == user_id)
    return query.all()


def get_team_keys_by_region(
    db: Session, team_id: int
) -> Dict[DBRegion, List[DBPrivateAIKey]]:
    """
    Get all keys for a team grouped by region.

    Args:
        db: Database session
        team_id: ID of the team to get keys for

    Returns:
        Dictionary mapping regions to lists of keys
    """
    # Get all keys for the team with their regions
    team_user_ids = (
        db.execute(select(DBUser.id).filter(DBUser.team_id == team_id)).scalars().all()
    )
    # Return keys owned by users in the team OR owned by the team
    team_keys = (
        db.query(DBPrivateAIKey)
        .filter(
            (DBPrivateAIKey.owner_id.in_(team_user_ids))
            | (DBPrivateAIKey.team_id == team_id)
        )
        .all()
    )

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

    logger.info(
        f"Found {len(team_keys)} keys in {len(keys_by_region)} regions for team {team_id}"
    )
    return keys_by_region


async def soft_delete_team(
    db: Session, team: DBTeam, current_time: datetime = None
) -> None:
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
    users_deactivated = (
        db.query(DBUser)
        .filter(DBUser.team_id == team.id)
        .update({"is_active": False}, synchronize_session=False)
    )
    logger.info(
        f"Deactivated {users_deactivated} users for soft-deleted team {team.id}"
    )

    # Commit the deletion and user deactivation
    db.commit()

    # Expire all keys in LiteLLM
    try:
        keys_by_region = get_team_keys_by_region(db, team.id)
        for region, keys in keys_by_region.items():
            try:
                litellm_service = LiteLLMService(
                    api_url=region.litellm_api_url, api_key=region.litellm_api_key
                )
                for key in keys:
                    if key.litellm_token:
                        try:
                            await litellm_service.update_key_duration(
                                litellm_token=key.litellm_token, duration="0d"
                            )
                            logger.info(
                                f"Expired key {key.id} in LiteLLM for soft-deleted team {team.id}"
                            )
                        except Exception as key_error:
                            logger.error(
                                f"Failed to expire key {key.id} in LiteLLM: {str(key_error)}"
                            )
            except Exception as region_error:
                logger.error(
                    f"Failed to expire keys in region {region.name}: {str(region_error)}"
                )
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
                    api_url=region.litellm_api_url, api_key=region.litellm_api_key
                )
                for key in keys:
                    if key.litellm_token:
                        try:
                            await litellm_service.update_key_duration(
                                litellm_token=key.litellm_token,
                                duration=f"{DEFAULT_KEY_DURATION}d",
                            )
                            logger.info(
                                f"Un-expired key {key.id} in LiteLLM for restored team {team.id}"
                            )
                        except Exception as key_error:
                            logger.error(
                                f"Failed to un-expire key {key.id} in LiteLLM: {str(key_error)}"
                            )
            except Exception as region_error:
                logger.error(
                    f"Failed to un-expire keys in region {region.name}: {str(region_error)}"
                )
    except Exception as restore_error:
        logger.error(
            f"Failed to un-expire keys for team {team.id}: {str(restore_error)}"
        )
        # Don't block restoration if key un-expiration fails

    # Reset deletion and warning timestamps and reactivate team
    team.deleted_at = None
    team.retention_warning_sent_at = None
    team.is_active = True
    team.updated_at = datetime.now(UTC)

    # Reactivate all users in the team
    users_reactivated = (
        db.query(DBUser)
        .filter(DBUser.team_id == team.id)
        .update({"is_active": True}, synchronize_session=False)
    )
    logger.info(f"Reactivated {users_reactivated} users for restored team {team.id}")

    db.commit()

    logger.info(f"Successfully restored team {team.id} ({team.name})")


async def propagate_team_budget_to_keys(
    db: Session,
    team_id: int,
    budget_amount: float,
    budget_duration: str,
    region_id: Optional[int] = None,
    update_key_limits: bool = True,
    apply_to_keys: bool = True,
) -> dict:
    """
    Propagate a team budget limit change to the LiteLLM team and optionally its keys.

    This function updates the LiteLLM team max_budget (shared ceiling), and
    optionally updates all keys (both user-owned and team-owned) with the new
    budget amount when a team's budget limit is changed.

    Args:
        db: Database session
        team_id: ID of the team whose keys should be updated
        budget_amount: New budget amount to set for all keys
        budget_duration: Budget duration string (e.g., "30d")
        region_id: Optional region ID to restrict updates to a single region.
            When provided the LiteLLM team for that region is updated even if
            the team currently has no keys there.
        update_key_limits: When True, also update each key max_budget. For
            POOL budgets this should be False so per-key overrides remain
            independent from shared team budget.
        apply_to_keys: Whether to propagate budget updates to keys in addition
            to the team budget.

    Returns:
        dict with "teams_updated" (number of LiteLLM teams successfully updated)
        and "errors" (list of error message strings).

    Note:
        Errors during updates are logged but don't raise exceptions.
        This ensures that limit updates succeed even if some updates fail.
    """
    teams_updated = 0
    errors: List[str] = []
    try:
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if not team:
            logger.error(f"Team {team_id} not found, skipping budget propagation")
            return {"teams_updated": 0, "errors": [f"Team {team_id} not found"]}

        if region_id is not None:
            # Update only the specified region, even if it currently has no keys.
            # Query keys for this region directly rather than loading all regions.
            region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
            if not region:
                logger.error(
                    f"Region {region_id} not found, skipping budget propagation"
                )
                return {
                    "teams_updated": 0,
                    "errors": [f"Region {region_id} not found"],
                }
            if apply_to_keys:
                team_user_ids = (
                    db.execute(select(DBUser.id).filter(DBUser.team_id == team_id))
                    .scalars()
                    .all()
                )
                region_keys = [
                    key
                    for key in (
                        db.query(DBPrivateAIKey)
                        .filter(
                            (DBPrivateAIKey.owner_id.in_(team_user_ids))
                            | (DBPrivateAIKey.team_id == team_id),
                            DBPrivateAIKey.region_id == region_id,
                        )
                        .all()
                    )
                    if key.litellm_token
                ]
            else:
                region_keys = []
            keys_by_region: Dict[DBRegion, List[DBPrivateAIKey]] = {region: region_keys}
        else:
            if apply_to_keys:
                keys_by_region = get_team_keys_by_region(db, team_id)
            else:
                # Only need the distinct regions; skip loading all key objects.
                team_user_ids = (
                    db.execute(select(DBUser.id).filter(DBUser.team_id == team_id))
                    .scalars()
                    .all()
                )
                region_ids = (
                    db.execute(
                        select(DBPrivateAIKey.region_id)
                        .filter(
                            (DBPrivateAIKey.owner_id.in_(team_user_ids))
                            | (DBPrivateAIKey.team_id == team_id),
                            DBPrivateAIKey.litellm_token.isnot(None),
                            DBPrivateAIKey.region_id.isnot(None),
                        )
                        .distinct()
                    )
                    .scalars()
                    .all()
                )
                regions = db.query(DBRegion).filter(DBRegion.id.in_(region_ids)).all()
                keys_by_region = {r: [] for r in regions}

        # Update team budget and keys for each region
        for region_obj, keys in keys_by_region.items():
            litellm_service = LiteLLMService(
                api_url=region_obj.litellm_api_url, api_key=region_obj.litellm_api_key
            )

            # Update the LiteLLM team budget (shared ceiling)
            lite_team_id = LiteLLMService.format_team_id(region_obj.name, team_id)
            try:
                await litellm_service.update_team_budget(
                    team_id=lite_team_id,
                    max_budget=budget_amount,
                    budget_duration=budget_duration,
                )
                teams_updated += 1
                logger.info(
                    f"Updated team {team_id} budget to {budget_amount} in LiteLLM region {region_obj.name}"
                )
            except Exception as team_error:
                errors.append(
                    f"Team {team_id}, region {region_obj.name}: {str(team_error)}"
                )
                logger.error(
                    f"Failed to update team {team_id} budget in region {region_obj.name}: {str(team_error)}"
                )

            if update_key_limits and apply_to_keys:
                # Update each key's budget via LiteLLM.
                for key in keys:
                    try:
                        await litellm_service.update_budget(
                            litellm_token=key.litellm_token,
                            budget_duration=budget_duration,
                            budget_amount=budget_amount,
                        )
                        logger.info(
                            f"Updated key {key.id} budget to {budget_amount} in LiteLLM after team budget limit change"
                        )
                    except Exception as key_error:
                        errors.append(f"Key {key.id}: {str(key_error)}")
                        logger.error(
                            f"Failed to update key {key.id} budget in LiteLLM: {str(key_error)}"
                        )
    except Exception as propagation_error:
        errors.append(str(propagation_error))
        logger.error(
            f"Error propagating budget limit to keys for team {team_id}: {str(propagation_error)}"
        )

    return {"teams_updated": teams_updated, "errors": errors}
