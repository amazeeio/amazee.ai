from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBTeamProduct, DBProduct
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType
from app.core.limit_service import LimitService, LimitNotFoundError
from fastapi import HTTPException, status
from typing import Optional
import logging
from prometheus_client import Counter

logger = logging.getLogger(__name__)

# Metrics to track which route is being followed
limit_check_route_counter = Counter(
    'resource_limits_check_route_total',
    'Total number of limit checks by route',
    ['function', 'route']
)

# Default limits across all customers and products
DEFAULT_USER_COUNT = 1
DEFAULT_KEYS_PER_USER = 1
DEFAULT_TOTAL_KEYS = 6
DEFAULT_SERVICE_KEYS = 5
DEFAULT_VECTOR_DB_COUNT = 5 # Setting to match service keys for drupal module trial
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
    # First try the new service, and short circuit if it works
    limit_service = LimitService(db)
    try:
        limit = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.USER)
        if not limit:
            limit_check_route_counter.labels(function='check_team_user_limit', route='limit_service_at_capacity').inc()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Team has reached their maximum user limit."
            )
        limit_check_route_counter.labels(function='check_team_user_limit', route='limit_service_success').inc()
        return
    except LimitNotFoundError as e:
        limit_check_route_counter.labels(function='check_team_user_limit', route='fallback').inc()
        logger.info(f"Team {team_id} has not been migrated to new limit system")
        logger.info(f"Exception thrown: {str(e)}")

    # Fall back to counting all users
    # Get current user count and max allowed users in a single query
    result = db.query(
        func.count(func.distinct(DBUser.id)).label('current_user_count'),
        func.coalesce(func.max(DBProduct.user_count), DEFAULT_USER_COUNT).label('max_users')
    ).select_from(DBTeam).filter(
        DBTeam.id == team_id
    ).outerjoin(
        DBTeamProduct,
        DBTeamProduct.team_id == DBTeam.id
    ).outerjoin(
        DBProduct,
        DBProduct.id == DBTeamProduct.product_id
    ).outerjoin(
        DBUser,
        DBUser.team_id == DBTeam.id
    ).first()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # At this point we know the limit needs to be created, and the values with which it should be created
    limit_service.set_limit(OwnerType.TEAM, team_id, ResourceType.USER, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_users, result.current_user_count)
    # Ensure the user in progress is recorded
    increment = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.USER)
    if (result.current_user_count >= result.max_users) and not increment:
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
    # First try the new service, and short circuit if it works
    limit_service = LimitService(db)
    try:
        if owner_id is not None:
            user_limit = limit_service.increment_resource(OwnerType.USER, owner_id, ResourceType.KEY)
            if not user_limit:
                limit_check_route_counter.labels(function='check_key_limits', route='limit_service_at_capacity').inc()
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Entity has reached their maximum number of AI keys"
                )
        else:
            team_limit = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.KEY)
            if not team_limit:
                limit_check_route_counter.labels(function='check_key_limits', route='limit_service_at_capacity').inc()
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Entity has reached their maximum number of AI keys"
                )
        limit_check_route_counter.labels(function='check_key_limits', route='limit_service_success').inc()
        return
    except LimitNotFoundError as e:
        limit_check_route_counter.labels(function='check_key_limits', route='fallback').inc()
        logger.info(f"Team {team_id} has not been migrated to new limit system")
        logger.info(f"Exception thrown: {str(e)}")

    # Get all limits and current counts in a single query
    result = db.query(
        func.coalesce(func.max(DBProduct.keys_per_user), DEFAULT_KEYS_PER_USER).label('max_keys_per_user'),
        func.coalesce(func.max(DBProduct.service_key_count), DEFAULT_SERVICE_KEYS).label('max_service_keys'),
        func.count(func.distinct(DBPrivateAIKey.id)).filter(
            DBPrivateAIKey.litellm_token.isnot(None)
        ).label('current_team_keys'),
        func.count(func.distinct(DBPrivateAIKey.id)).filter(
            DBPrivateAIKey.owner_id == owner_id,
            DBPrivateAIKey.litellm_token.isnot(None)
        ).label('current_user_keys') if owner_id else None,
        func.count(func.distinct(DBPrivateAIKey.id)).filter(
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

    # At this point we know the limit needs to be created, and the values with which it should be created
    if owner_id is not None:
        # Create user-level limit
        limit_service.set_limit(OwnerType.USER, owner_id, ResourceType.KEY, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_keys_per_user, result.current_user_keys)
        # Ensure the key in progress is recorded
        increment = limit_service.increment_resource(OwnerType.USER, owner_id, ResourceType.KEY)
        if (result.current_user_keys >= result.max_keys_per_user) and not increment:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"User has reached the maximum LLM key limit of {result.max_keys_per_user} keys"
            )
    else:
        # Create team-level limit
        limit_service.set_limit(OwnerType.TEAM, team_id, ResourceType.KEY, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_service_keys, result.current_service_keys)
        # Ensure the key in progress is recorded
        increment = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.KEY)
        # Check service key limits (only for team-owned keys)
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
    # First try the new service, and short circuit if it works
    limit_service = LimitService(db)
    try:
        limit = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB)
        if not limit:
            limit_check_route_counter.labels(function='check_vector_db_limits', route='limit_service_at_capacity').inc()
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Team has reached their maximum vector DB limit."
            )
        limit_check_route_counter.labels(function='check_vector_db_limits', route='limit_service_success').inc()
        return
    except LimitNotFoundError as e:
        limit_check_route_counter.labels(function='check_vector_db_limits', route='fallback').inc()
        logger.info(f"Team {team_id} has not been migrated to new limit system")
        logger.info(f"Exception thrown: {str(e)}")

    # Get vector DB limits and current count in a single query
    result = db.query(
        func.coalesce(func.max(DBProduct.vector_db_count), DEFAULT_VECTOR_DB_COUNT).label('max_vector_db_count'),
        func.count(func.distinct(DBPrivateAIKey.id)).filter(
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

    # At this point we know the limit needs to be created, and the values with which it should be created
    limit_service.set_limit(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_vector_db_count, result.current_vector_db_count)
    # Ensure the vector DB in progress is recorded
    increment = limit_service.increment_resource(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB)
    if (result.current_vector_db_count >= result.max_vector_db_count) and not increment:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Team has reached the maximum vector DB limit of {result.max_vector_db_count} databases"
        )

def get_token_restrictions(db: Session, team_id: int) -> tuple[int, float, int]:
    """
    Get the token restrictions for a team.
    """
    # First try to get budget and RPM limits from the new service
    limit_service = LimitService(db)
    max_spend = None
    rpm_limit = None

    try:
        # Try to get team limits from limit service
        team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
        if team:
            team_limits = limit_service.get_team_limits(team)
            for limit in team_limits.limits:
                if limit.resource == ResourceType.BUDGET:
                    max_spend = limit.max_value
                elif limit.resource == ResourceType.RPM:
                    rpm_limit = limit.max_value
        if max_spend is not None or rpm_limit is not None:
            limit_check_route_counter.labels(function='get_token_restrictions', route='limit_service_success').inc()
        else:
            limit_check_route_counter.labels(function='get_token_restrictions', route='fallback').inc()
    except Exception as e:
        limit_check_route_counter.labels(function='get_token_restrictions', route='fallback').inc()
        logger.info(f"Could not get limits from limit service for team {team_id}: {str(e)}")

    # Get all token restrictions in a single query (for duration and fallback values)
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

    # Use limit service values if available, otherwise fall back to product/default values
    final_max_spend = max_spend if max_spend is not None else result.max_max_spend
    final_rpm_limit = rpm_limit if rpm_limit is not None else result.max_rpm_limit

    return result.max_key_duration, final_max_spend, final_rpm_limit

def get_default_team_limit_for_resource(resource_type: ResourceType) -> float:
    if resource_type == ResourceType.KEY:
        return DEFAULT_SERVICE_KEYS  # Changed from DEFAULT_TOTAL_KEYS
    elif resource_type == ResourceType.VECTOR_DB:
        return DEFAULT_VECTOR_DB_COUNT
    elif resource_type == ResourceType.USER:
        return DEFAULT_USER_COUNT
    elif resource_type == ResourceType.BUDGET:
        return DEFAULT_MAX_SPEND
    elif resource_type == ResourceType.RPM:
        return DEFAULT_RPM_PER_KEY
    else:
        raise ValueError(f"Unknown resource type {resource_type.value}")

def get_default_user_limit_for_resource(resource_type: ResourceType) -> float:
    if resource_type == ResourceType.KEY:
        return DEFAULT_KEYS_PER_USER  # Different from team service keys
    else:
        raise ValueError(f"Unsupported resource type \"{resource_type.value}\" for user")

def get_team_product_limit_for_resource(db: Session, team_id: int, resource_type: ResourceType) -> Optional[float]:
    if resource_type == ResourceType.KEY:
        # For team keys, use service_key_count
        query = db.query(func.max(DBProduct.service_key_count))
    elif resource_type == ResourceType.VECTOR_DB:
        query = db.query(func.max(DBProduct.vector_db_count))
    elif resource_type == ResourceType.USER:
        query = db.query(func.max(DBProduct.user_count))
    elif resource_type == ResourceType.BUDGET:
        query = db.query(func.max(DBProduct.max_budget_per_key))
    elif resource_type == ResourceType.RPM:
        query = db.query(func.max(DBProduct.rpm_per_key))
    else:
        raise ValueError(f"Unknown resource type {resource_type.value}")

    result = query.join(
        DBTeamProduct, DBTeamProduct.product_id == DBProduct.id
    ).filter(
        DBTeamProduct.team_id == team_id
    ).scalar()

    return result

def get_user_product_limit_for_resource(db: Session, team_id: int, resource_type: ResourceType) -> Optional[float]:
    if resource_type == ResourceType.KEY:
        # For user keys, use keys_per_user
        query = db.query(func.max(DBProduct.keys_per_user))
    else:
        # For all other resources (VECTOR_DB, BUDGET, RPM), users inherit from team
        # or have overrides, so we return None to indicate they should use team limits
        raise ValueError(f"Unsupported resource type \"{resource_type.value}\" for user")

    result = query.join(
        DBTeamProduct, DBTeamProduct.product_id == DBProduct.id
    ).filter(
        DBTeamProduct.team_id == team_id
    ).scalar()

    return result
