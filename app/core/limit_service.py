from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, select, func
from fastapi import HTTPException, status
from typing import Optional, List
from datetime import datetime, UTC
from app.db.models import DBLimitedResource, DBUser, DBTeam, DBTeamProduct, DBPrivateAIKey, DBProduct
from app.schemas.limits import (
    LimitedResource,
    LimitedResourceCreate,
    OwnerType,
    UnitType,
    LimitSource,
    LimitType,
    ResourceType
    )
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


class LimitNotFoundError(Exception):
    """Raised when a requested limit is not found."""
    pass


class LimitService:
    """
    Core service for managing resource limits according to the design document.

    Implements the following hierarchy:
    - MANUAL limits can override anything
    - PRODUCT limits can override DEFAULT
    - DEFAULT limits are the fallback

    Users inherit team limits unless they have individual overrides.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_team_limits(self, team: DBTeam) -> List[LimitedResource]:
        """
        Get all effective limits for a team.

        Args:
            team_id: ID of the team

        Returns:
            List of LimitedResource objects containing all limits for the team
        """
        # Get system default limits
        system_limits = self.db.query(DBLimitedResource).filter(
            DBLimitedResource.owner_type == OwnerType.SYSTEM
        ).all()

        # Get team limits
        team_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id
            )
        ).all()

        # Build effective limits: TEAM -> SYSTEM inheritance (no user limits in team view)
        effective_limits = {}

        # Start with system defaults
        for limit in system_limits:
            effective_limits[limit.resource] = limit

        # Override with team limits
        for limit in team_limits:
            effective_limits[limit.resource] = limit

        limits = list(effective_limits.values())

        # Convert to schemas and create team limits for system limits
        limit_schemas = []
        for limit in limits:
            # If this is a system limit, create a team limit so the admin can edit it
            # (Since we're iterating through effective_limits, any system limit here means no team override exists)
            if limit.owner_type == OwnerType.SYSTEM:
                # Create a team limit based on the system limit
                team_limit = self.set_limit(
                    owner_type=OwnerType.TEAM,
                    owner_id=team.id,
                    resource_type=limit.resource,
                    limit_type=limit.limit_type,
                    unit=limit.unit,
                    max_value=limit.max_value,
                    current_value=limit.current_value,
                    limited_by=LimitSource.DEFAULT  # Inherit the source from system limit
                )
                schema = LimitedResource.model_validate(team_limit)
            else:
                schema = LimitedResource.model_validate(limit)

            limit_schemas.append(schema)

        return limit_schemas

    def get_user_limits(self, user: DBUser) -> List[LimitedResource]:
        """
        Get all effective limits for a user.
        Users inherit team limits unless they have individual overrides.

        Args:
            user_id: ID of the user

        Returns:
            List of LimitedResource objects containing effective limits for the user
        """
        # Get user-specific limits
        user_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.USER,
                DBLimitedResource.owner_id == user.id
            )
        ).all()

        # Get team limits
        team = self.db.query(DBTeam).filter(DBTeam.id == user.team_id).first()
        if team:
            team_limits = self.get_team_limits(team)
        else:
            team_limits = []

        # Build effective limits: USER -> TEAM -> SYSTEM inheritance
        effective_limits = {}

        # team limits already went through the system limits
        for limit in team_limits:
            effective_limits[limit.resource] = limit

        # Override with user-specific limits
        for limit in user_limits:
            effective_limits[limit.resource] = limit

        limit_schemas = [
            LimitedResource.model_validate(limit)
            for limit in effective_limits.values()
        ]

        return limit_schemas

    def get_system_limits(self) -> List[LimitedResource]:
        """
        Get all system default limits.

        Returns:
            List of LimitedResource objects containing all system limits
        """
        system_limits = self.db.query(DBLimitedResource).filter(
            DBLimitedResource.owner_type == OwnerType.SYSTEM
        ).all()

        limit_schemas = [
            LimitedResource.model_validate(limit) for limit in system_limits
        ]

        return limit_schemas

    def set_current_value(self, limit: LimitedResource, new_value: float):
        """
        Updates the current_value of a limit to a precise value.

        Raises:
            ValueError: When trying to set a COUNT CONTROL_PLANE limit which was not previously 0
        """
        if limit.limit_type == LimitType.CONTROL_PLANE and limit.unit == UnitType.COUNT and limit.current_value > 0.0:
            raise ValueError("Control Plane counters must be incremented or decremented, and can only be set once.")
        db_limit = self.db.execute(select(DBLimitedResource).filter(DBLimitedResource.id == limit.id)).scalar_one()
        db_limit.current_value = new_value
        self.db.commit()

    def increment_resource(self, owner_type: OwnerType, owner_id: int, resource_type: ResourceType) -> bool:
        """
        Increment the current value of a resource if within limits.

        Only COUNT type Control Plane resources can be incremented.
        Data Plane resources cannot be incremented/decremented.

        Args:
            owner_type: OwnerType enum
            owner_id: ID of the owner
            resource_type: ResourceType enum

        Returns:
            True if increment succeeded, False if at capacity

        Raises:
            ValueError: If trying to increment Data Plane resources or non-COUNT type resources
        """
        limit = self._verify_control_plane_count(owner_type, owner_id, resource_type)

        # Control Plane limits: check capacity
        if limit.current_value >= limit.max_value:
            return False

        # Increment and save
        limit.current_value += 1
        limit.updated_at = datetime.now(UTC)
        self.db.commit()

        return True

    def decrement_resource(self, owner_type: OwnerType, owner_id: int, resource_type: ResourceType) -> bool:
        """
        Decrement the current value of a resource.

        Only COUNT type Control Plane resources can be decremented.
        Data Plane resources cannot be incremented/decremented.

        Args:
            owner_type: OwnerType enum
            owner_id: ID of the owner
            resource_type: ResourceType enum

        Returns:
            True if decrement succeeded

        Raises:
            ValueError: If trying to decrement Data Plane resources or non-COUNT type resources
        """
        limit = self._verify_control_plane_count(owner_type, owner_id, resource_type)

        # Control Plane limits: decrement (but not below 0)
        if limit.current_value > 0:
            limit.current_value -= 1
            limit.updated_at = datetime.now(UTC)
            self.db.commit()

        return True

    def _verify_control_plane_count(self, owner_type: OwnerType, owner_id: int, resource: ResourceType) -> DBLimitedResource:
        limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource
            )
        ).first()

        if not limit:
            raise LimitNotFoundError(f"No limit found for {owner_type} {owner_id} resource {resource}")

        # Data Plane limits cannot be incremented/decremented
        if limit.limit_type == LimitType.DATA_PLANE:
            raise ValueError("Cannot increment/decrement Data Plane resources")

        # Only COUNT type resources can be incremented/decremented
        if limit.unit != UnitType.COUNT:
            raise ValueError("Only COUNT type resources can be incremented/decremented")

        return limit

    def set_limit(
        self,
        owner_type: OwnerType,
        owner_id: int,
        resource_type: ResourceType,
        limit_type: LimitType,
        unit: UnitType,
        max_value: float,
        current_value: Optional[float] = None,
        limited_by: LimitSource = LimitSource.DEFAULT,
        set_by: Optional[str] = None
    ) -> DBLimitedResource:
        logger.info(f"Overwriting {resource_type.value} limit for {owner_type.value} {owner_id}")
        limit = LimitedResourceCreate(
            limit_type=limit_type,
            resource=resource_type,
            unit=unit,
            max_value=max_value,
            current_value=current_value,
            owner_type=owner_type,
            owner_id=owner_id,
            limited_by=limited_by,
            set_by=set_by
        )
        return self._set_limit(limited_resource=limit)

    def _set_limit(self, limited_resource: LimitedResourceCreate) -> DBLimitedResource:
        """
        Create or update a limit following source hierarchy rules.

        Source hierarchy: MANUAL > PRODUCT > DEFAULT
        - MANUAL can override anything
        - PRODUCT can override DEFAULT
        - DEFAULT cannot override anything

        Args:
            limited_resource: A Pydantic type-checked LimitedResource object

        Returns:
            The created or updated limit

        Raises:
            ValueError: If validation fails or hierarchy rules are violated
        """
        # Validate inputs
        if limited_resource.limit_type == LimitType.CONTROL_PLANE and limited_resource.current_value is None:
            raise ValueError("Control plane limits must have current_value")

        if limited_resource.limited_by == LimitSource.MANUAL and not limited_resource.set_by:
            raise ValueError("Manual limits must specify set_by")

        # Business rule: SYSTEM limits can only have DEFAULT or MANUAL source
        if limited_resource.owner_type == OwnerType.SYSTEM and limited_resource.limited_by not in [LimitSource.DEFAULT, LimitSource.MANUAL]:
            raise ValueError("SYSTEM limits can only have DEFAULT or MANUAL source")

        # Check if limit already exists
        existing_limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == limited_resource.owner_type,
                DBLimitedResource.owner_id == limited_resource.owner_id,
                DBLimitedResource.resource == limited_resource.resource
            )
        ).first()

        if existing_limit:
            # Apply hierarchy rules
            if existing_limit.limited_by == LimitSource.MANUAL and limited_resource.limited_by != LimitSource.MANUAL:
                raise ValueError("Cannot override manual limit with non-manual limit")

            if existing_limit.limited_by == LimitSource.PRODUCT and limited_resource.limited_by == LimitSource.DEFAULT:
                raise ValueError("Cannot override product limit with default limit")

            # Update existing limit
            existing_limit.limit_type = limited_resource.limit_type
            existing_limit.unit = limited_resource.unit
            existing_limit.max_value = limited_resource.max_value
            existing_limit.current_value = limited_resource.current_value
            existing_limit.limited_by = limited_resource.limited_by
            existing_limit.set_by = limited_resource.set_by if limited_resource.limited_by == LimitSource.MANUAL else None
            existing_limit.updated_at = datetime.now(UTC)

            self.db.add(existing_limit)
            self.db.commit()
            result = existing_limit

        else:
            # Create new limit
            new_limit = DBLimitedResource(
                limit_type=limited_resource.limit_type,
                resource=limited_resource.resource,
                unit=limited_resource.unit,
                max_value=limited_resource.max_value,
                current_value=limited_resource.current_value,
                owner_type=limited_resource.owner_type,
                owner_id=limited_resource.owner_id,
                limited_by=limited_resource.limited_by,
                set_by=limited_resource.set_by if limited_resource.limited_by == LimitSource.MANUAL else None,
                created_at=datetime.now(UTC)
            )

            self.db.add(new_limit)
            self.db.commit()
            result = new_limit

        # If this is a system limit change, update all default limits for the same resource
        if limited_resource.owner_type == OwnerType.SYSTEM:
            self._update_default_limits_for_resource(limited_resource.resource, limited_resource.max_value)

        return result

    def _update_default_limits_for_resource(self, resource_type: ResourceType, new_max_value: float) -> None:
        """
        Update all team and user limits that have limited_by=DEFAULT for the given resource type
        to reflect the new system default value.

        Args:
            resource_type: The resource type to update
            new_max_value: The new maximum value from the system limit
        """
        # Find all team and user limits with limited_by=DEFAULT for this resource
        default_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.resource == resource_type,
                DBLimitedResource.limited_by == LimitSource.DEFAULT,
                DBLimitedResource.owner_type.in_([OwnerType.TEAM, OwnerType.USER])
            )
        ).all()

        # Update each default limit to reflect the new system default
        for limit in default_limits:
            limit.max_value = new_max_value
            limit.updated_at = datetime.now(UTC)
            self.db.add(limit)

        if default_limits:
            self.db.commit()
            logger.info(f"Updated {len(default_limits)} default limits for resource {resource_type.value} to new system default {new_max_value}")

    def reset_team_limits(self, team: DBTeam) -> List[LimitedResource]:
        """
        Reset all limits for a team following cascade rules.
        MANUAL -> PRODUCT -> DEFAULT based on availability.

        Args:
            team_id: ID of the team

        Returns:
            List of updated LimitedResource objects for the team
        """
        team_limits = self.get_team_limits(team)
        for limit in team_limits:
            self._reset_limit(limit)

        # Do a fresh pull from the db after the updates
        return self.get_team_limits(team)

    def _reset_limit(self, limit: LimitedResource) -> DBLimitedResource:
        """
        Reset a specific limit following cascade rules.
        MANUAL -> PRODUCT -> DEFAULT based on availability.

        Args:
            limit: LimitedResource Pydantic model to reset

        Returns:
            Updated DBLimitedResource database model
        """
        if limit.owner_type == OwnerType.SYSTEM:
            raise ValueError("Cannot reset SYSTEM limits")
        elif limit.owner_type == OwnerType.TEAM:
            logger.info(f"Setting resource {limit.resource} to product max for team {limit.owner_id}")
            team_id = limit.owner_id
        elif limit.owner_type == OwnerType.USER:
            logger.info(f"Trying to reset {limit.resource} limits for user {limit.owner_id}")
            user = self.db.query(DBUser).filter(DBUser.id == limit.owner_id).first()
            if not user.team_id:
                logger.warning(f"User {limit.owner_id} is a system user; cannot reset limit")
                # Find and return the existing database model
                return self.db.query(DBLimitedResource).filter(DBLimitedResource.id == limit.id).first()
            team_id = user.team_id
        else:
            raise ValueError(f"Unknown owner type, cannot reset limit {limit}")

        # Calculate new values
        if limit.owner_type == OwnerType.USER and limit.resource == ResourceType.USER_KEY:
            max_value = self.get_user_product_limit_for_resource(team_id, limit.resource)
            if max_value is None:
                max_value = self.get_default_user_limit_for_resource(limit.resource)
                new_limited_by = LimitSource.DEFAULT
            else:
                new_limited_by = LimitSource.PRODUCT
        else:
            max_value = self.get_team_product_limit_for_resource(team_id, limit.resource)
            if max_value is None:
                max_value = self.get_default_team_limit_for_resource(limit.resource)
                new_limited_by = LimitSource.DEFAULT
            else:
                new_limited_by = LimitSource.PRODUCT

        # Find the corresponding database model and update it
        db_limit = self.db.query(DBLimitedResource).filter(DBLimitedResource.id == limit.id).first()
        if db_limit:
            db_limit.max_value = max_value
            db_limit.limited_by = new_limited_by
            db_limit.set_by = "reset"
            self.db.commit()
            return db_limit
        else:
            raise ValueError(f"Database model not found for limit {limit.id}")

    def reset_limit(self, owner_type: OwnerType, owner_id: int, resource_type: ResourceType) -> DBLimitedResource:
        limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if not limit:
            raise LimitNotFoundError(f"No limit found for {owner_type} {owner_id} resource {resource_type}")

        return self._reset_limit(limit)

    def set_team_limits(self, team: DBTeam):
        """
        Goes through all available limits and applies them for a team. Will apply PRODUCT values by preference,
        falling back to DEFAULT. Will not override MANUAL.
        Intended to be used by the automated workflows when product associations change, or simply on a regular
        cadence, this keeps everything in-sync to allow us to rely on the increment/decrement methods for CP limit
        management.

        Args:
            team: The team for which limits are being applied
        """
        existing_limits = self.get_team_limits(team)

        # Define all resource types that need limits
        # Only include resources that are supported by the resource_limits functions
        all_resources = [
            ResourceType.USER,
            ResourceType.SERVICE_KEY,
            ResourceType.VECTOR_DB,
            ResourceType.BUDGET,
            ResourceType.RPM
        ]

        # Process each resource type
        for resource_type in all_resources:
            # Skip if manual limit already exists
            existing_limit = next(
                (limit for limit in existing_limits if limit.resource == resource_type),
                None
            )
            if existing_limit and existing_limit.limited_by == LimitSource.MANUAL:
                continue

            # Determine limit type and unit based on resource
            if resource_type in [ResourceType.USER, ResourceType.SERVICE_KEY, ResourceType.VECTOR_DB, ResourceType.GPT_INSTANCE]:
                limit_type = LimitType.CONTROL_PLANE
                unit = UnitType.COUNT
                # Preserve existing current_value if updating, otherwise set to 0.0
                if existing_limit and existing_limit.current_value is not None:
                    current_value = existing_limit.current_value
                else:
                    current_value = 0.0  # CP limits need current_value
            else:
                limit_type = LimitType.DATA_PLANE
                unit = self._get_unit_for_resource(resource_type)
                # For DP limits, preserve existing current_value if it exists
                if existing_limit and existing_limit.current_value is not None:
                    current_value = existing_limit.current_value
                else:
                    current_value = None  # DP limits don't track current value by default

            # Try to get product limit first, fall back to default
            max_value = self.get_team_product_limit_for_resource(team.id, resource_type)
            if max_value is not None:
                limit_source = LimitSource.PRODUCT
            else:
                max_value = self.get_default_team_limit_for_resource(resource_type)
                limit_source = LimitSource.DEFAULT

            # Set the limit (this will update existing or create new)
            self.set_limit(
                owner_type=OwnerType.TEAM,
                owner_id=team.id,
                resource_type=resource_type,
                limit_type=limit_type,
                unit=unit,
                max_value=max_value,
                current_value=current_value,
                limited_by=limit_source
            )

    def set_user_limits(self, user: DBUser):
        """
        Goes through all available user limits and applies them for a user. Will apply PRODUCT values by preference,
        falling back to DEFAULT. Will not override MANUAL.
        Intended to be used when a new user is created to set up their default limits.

        Args:
            user: The user for which limits are being applied
        """
        existing_limits = self.get_user_limits(user)

        # Define all resource types that need user limits
        # Users only get USER_KEY limits - BUDGET and RPM are inherited from team
        user_resources = [
            ResourceType.USER_KEY
        ]

        # Process each resource type
        for resource_type in user_resources:
            # Skip if manual limit already exists
            existing_limit = next(
                (limit for limit in existing_limits if limit.resource == resource_type),
                None
            )
            if existing_limit and existing_limit.limited_by == LimitSource.MANUAL:
                continue

            # Determine limit type and unit based on resource
            if resource_type == ResourceType.USER_KEY:
                limit_type = LimitType.CONTROL_PLANE
                unit = UnitType.COUNT
                # Preserve existing current_value if updating, otherwise set to 0.0
                if existing_limit and existing_limit.current_value is not None:
                    current_value = existing_limit.current_value
                else:
                    current_value = 0.0  # CP limits need current_value
            else:
                limit_type = LimitType.DATA_PLANE
                unit = self._get_unit_for_resource(resource_type)
                # For DP limits, preserve existing current_value if it exists
                if existing_limit and existing_limit.current_value is not None:
                    current_value = existing_limit.current_value
                else:
                    current_value = None  # DP limits don't track current value by default

            # For users, only KEY limits are set - BUDGET and RPM are inherited from team
            max_value = self.get_user_product_limit_for_resource(user.team_id, resource_type)
            if max_value is not None:
                limit_source = LimitSource.PRODUCT
            else:
                max_value = self.get_default_user_limit_for_resource(resource_type)
                limit_source = LimitSource.DEFAULT

            # Set the limit (this will update existing or create new)
            self.set_limit(
                owner_type=OwnerType.USER,
                owner_id=user.id,
                resource_type=resource_type,
                limit_type=limit_type,
                unit=unit,
                max_value=max_value,
                current_value=current_value,
                limited_by=limit_source
            )

    def _get_unit_for_resource(self, resource_type: ResourceType) -> UnitType:
        """
        Get the appropriate unit type for a given resource type.

        Args:
            resource_type: The resource type

        Returns:
            The appropriate unit type
        """
        if resource_type == ResourceType.BUDGET:
            return UnitType.DOLLAR
        elif resource_type == ResourceType.STORAGE:
            return UnitType.GB
        else:
            return UnitType.COUNT

    def check_team_user_limit(self, team_id: int) -> None:
        """
        Check if adding a user would exceed the team's product limits.
        Raises HTTPException if the limit would be exceeded.

        Args:
            team_id: ID of the team to check
        """
        # First try the new service, and short circuit if it works
        try:
            limit = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.USER)
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
        result = self.db.query(
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
        self.set_limit(OwnerType.TEAM, team_id, ResourceType.USER, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_users, result.current_user_count)
        # Ensure the user in progress is recorded
        increment = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.USER)
        if (result.current_user_count >= result.max_users) and not increment:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Team has reached the maximum user limit of {result.max_users} users"
            )

    def check_key_limits(self, team_id: int, owner_id: Optional[int] = None) -> None:
        """
        Check if creating a new LLM token would exceed the team's or user's key limits.
        Raises HTTPException if any limit would be exceeded.

        Args:
            team_id: ID of the team to check
            owner_id: Optional ID of the user who will own the key
        """
        # First try the new service, and short circuit if it works
        try:
            if owner_id is not None:
                user_limit = self.increment_resource(OwnerType.USER, owner_id, ResourceType.USER_KEY)
                if not user_limit:
                    limit_check_route_counter.labels(function='check_key_limits', route='limit_service_at_capacity').inc()
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail=f"Entity has reached their maximum number of AI keys"
                    )
            else:
                team_limit = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.SERVICE_KEY)
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
        result = self.db.query(
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
                    self.db.query(DBUser.id).filter(DBUser.team_id == DBTeam.id)
                )
            )
        ).first()

        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        # At this point we know the limit needs to be created, and the values with which it should be created
        if owner_id is not None:
            # Create user-level limit
            self.set_limit(OwnerType.USER, owner_id, ResourceType.USER_KEY, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_keys_per_user, result.current_user_keys)
            # Ensure the key in progress is recorded
            increment = self.increment_resource(OwnerType.USER, owner_id, ResourceType.USER_KEY)
            if (result.current_user_keys >= result.max_keys_per_user) and not increment:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"User has reached the maximum LLM key limit of {result.max_keys_per_user} keys"
                )
        else:
            # Create team-level limit
            self.set_limit(OwnerType.TEAM, team_id, ResourceType.SERVICE_KEY, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_service_keys, result.current_service_keys)
            # Ensure the key in progress is recorded
            increment = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.SERVICE_KEY)
            # Check service key limits (only for team-owned keys)
            if owner_id is None and result.current_service_keys >= result.max_service_keys:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Team has reached the maximum service LLM key limit of {result.max_service_keys} keys"
                )

    def check_vector_db_limits(self, team_id: int) -> None:
        """
        Check if creating a new vector DB would exceed the team's vector DB limits.
        Raises HTTPException if the limit would be exceeded.

        Args:
            team_id: ID of the team to check
        """
        # First try the new service, and short circuit if it works
        try:
            limit = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB)
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
        result = self.db.query(
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
                    self.db.query(DBUser.id).filter(DBUser.team_id == DBTeam.id)
                )
            )
        ).first()

        if not result:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

        # At this point we know the limit needs to be created, and the values with which it should be created
        self.set_limit(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB, LimitType.CONTROL_PLANE, UnitType.COUNT, result.max_vector_db_count, result.current_vector_db_count)
        # Ensure the vector DB in progress is recorded
        increment = self.increment_resource(OwnerType.TEAM, team_id, ResourceType.VECTOR_DB)
        if (result.current_vector_db_count >= result.max_vector_db_count) and not increment:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Team has reached the maximum vector DB limit of {result.max_vector_db_count} databases"
            )

    def get_token_restrictions(self, team_id: int) -> tuple[int, float, int]:
        """
        Get the token restrictions for a team.
        """
        # First try to get budget and RPM limits from the new service
        max_spend = None
        rpm_limit = None

        try:
            # Try to get team limits from limit service
            team = self.db.query(DBTeam).filter(DBTeam.id == team_id).first()
            if team:
                team_limits = self.get_team_limits(team)
                for limit in team_limits:
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
        result = self.db.query(
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

    def get_default_team_limit_for_resource(self, resource_type: ResourceType) -> float:
        """
        Get the default team limit for a resource from the database SYSTEM limits.
        Falls back to hardcoded constants if no SYSTEM limit exists.
        """
        # Try to get from database first
        system_limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.SYSTEM,
                DBLimitedResource.owner_id == 0,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if system_limit:
            return system_limit.max_value

        # Fallback to hardcoded constants if no SYSTEM limit exists
        if resource_type == ResourceType.SERVICE_KEY:
            return DEFAULT_SERVICE_KEYS
        elif resource_type == ResourceType.VECTOR_DB:
            return DEFAULT_VECTOR_DB_COUNT
        elif resource_type == ResourceType.USER:
            return DEFAULT_USER_COUNT
        elif resource_type == ResourceType.USER_KEY:
            return DEFAULT_KEYS_PER_USER
        elif resource_type == ResourceType.BUDGET:
            return DEFAULT_MAX_SPEND
        elif resource_type == ResourceType.RPM:
            return DEFAULT_RPM_PER_KEY
        else:
            raise ValueError(f"Unknown resource type {resource_type.value}")

    def get_default_user_limit_for_resource(self, resource_type: ResourceType) -> float:
        """
        Get the default user limit for a resource from the database SYSTEM limits.
        Falls back to hardcoded constants if no SYSTEM limit exists.
        """
        # Try to get from database first
        system_limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.SYSTEM,
                DBLimitedResource.owner_id == 0,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if system_limit:
            return system_limit.max_value

        # Fallback to hardcoded constants if no SYSTEM limit exists
        if resource_type == ResourceType.USER_KEY:
            return DEFAULT_KEYS_PER_USER
        else:
            raise ValueError(f"Unsupported resource type \"{resource_type.value}\" for user")

    def get_team_product_limit_for_resource(self, team_id: int, resource_type: ResourceType) -> Optional[float]:
        if resource_type == ResourceType.SERVICE_KEY:
            # For team keys, use service_key_count
            query = self.db.query(func.max(DBProduct.service_key_count))
        elif resource_type == ResourceType.VECTOR_DB:
            query = self.db.query(func.max(DBProduct.vector_db_count))
        elif resource_type == ResourceType.USER:
            query = self.db.query(func.max(DBProduct.user_count))
        elif resource_type == ResourceType.USER_KEY:
            # For user keys at team level, use keys_per_user
            query = self.db.query(func.max(DBProduct.keys_per_user))
        elif resource_type == ResourceType.BUDGET:
            query = self.db.query(func.max(DBProduct.max_budget_per_key))
        elif resource_type == ResourceType.RPM:
            query = self.db.query(func.max(DBProduct.rpm_per_key))
        else:
            # Allow for default values which might not be included on a product.
            return None

        result = query.join(
            DBTeamProduct, DBTeamProduct.product_id == DBProduct.id
        ).filter(
            DBTeamProduct.team_id == team_id
        ).scalar()

        return result

    def get_user_product_limit_for_resource(self, team_id: int, resource_type: ResourceType) -> Optional[float]:
        if resource_type == ResourceType.USER_KEY:
            # For user keys, use keys_per_user
            query = self.db.query(func.max(DBProduct.keys_per_user))
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


def setup_default_limits(db: Session) -> None:
    """
    Setup default system limits if ENABLE_LIMITS is true.
    This function ensures that all default limits exist in the database
    using the current constant values.

    Args:
        db: Database session
    """
    import os

    # Only run if ENABLE_LIMITS is true
    if not os.getenv('ENABLE_LIMITS', 'false').lower() in ('true', '1', 'yes'):
        logger.info("ENABLE_LIMITS is not set to true, skipping default limits setup")
        return

    logger.info("Setting up default system limits")

    limit_service = LimitService(db)

    # Define all default limits to create
    default_limits = [
        # Control Plane limits
        {
            'resource': ResourceType.USER,
            'limit_type': LimitType.CONTROL_PLANE,
            'unit': UnitType.COUNT,
            'max_value': DEFAULT_USER_COUNT,
            'current_value': 0.0
        },
        {
            'resource': ResourceType.USER_KEY,
            'limit_type': LimitType.CONTROL_PLANE,
            'unit': UnitType.COUNT,
            'max_value': DEFAULT_KEYS_PER_USER,
            'current_value': 0.0
        },
        {
            'resource': ResourceType.SERVICE_KEY,
            'limit_type': LimitType.CONTROL_PLANE,
            'unit': UnitType.COUNT,
            'max_value': DEFAULT_SERVICE_KEYS,
            'current_value': 0.0
        },
        {
            'resource': ResourceType.VECTOR_DB,
            'limit_type': LimitType.CONTROL_PLANE,
            'unit': UnitType.COUNT,
            'max_value': DEFAULT_VECTOR_DB_COUNT,
            'current_value': 0.0
        },
        # Data Plane limits
        {
            'resource': ResourceType.BUDGET,
            'limit_type': LimitType.DATA_PLANE,
            'unit': UnitType.DOLLAR,
            'max_value': DEFAULT_MAX_SPEND,
            'current_value': None
        },
        {
            'resource': ResourceType.RPM,
            'limit_type': LimitType.DATA_PLANE,
            'unit': UnitType.COUNT,
            'max_value': DEFAULT_RPM_PER_KEY,
            'current_value': None
        }
    ]

    # Create each default limit if it doesn't exist
    for limit_config in default_limits:
        try:
            # Check if limit already exists
            existing_limit = db.query(DBLimitedResource).filter(
                and_(
                    DBLimitedResource.owner_type == OwnerType.SYSTEM,
                    DBLimitedResource.owner_id == 0,
                    DBLimitedResource.resource == limit_config['resource']
                )
            ).first()

            if not existing_limit:
                # Create the limit
                limit_service.set_limit(
                    owner_type=OwnerType.SYSTEM,
                    owner_id=0,
                    resource_type=limit_config['resource'],
                    limit_type=limit_config['limit_type'],
                    unit=limit_config['unit'],
                    max_value=limit_config['max_value'],
                    current_value=limit_config['current_value'],
                    limited_by=LimitSource.DEFAULT
                )
                logger.info(f"Created default limit for {limit_config['resource'].value}")
            else:
                logger.debug(f"Default limit for {limit_config['resource'].value} already exists")

        except Exception as e:
            logger.error(f"Failed to create default limit for {limit_config['resource'].value}: {str(e)}")
            raise

    logger.info("Default system limits setup completed")
