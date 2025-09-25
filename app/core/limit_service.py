from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, select
from fastapi import HTTPException, status
from typing import Optional, List
from datetime import datetime, UTC
from app.db.models import DBLimitedResource, DBUser, DBTeam, DBTeamProduct
from app.schemas.limits import (
    LimitedResource,
    LimitedResourceBase,
    TeamLimits,
    OwnerType,
    UnitType,
    LimitSource,
    LimitType,
    ResourceType
    )
from app.core.resource_limits import (
    get_default_team_limit_for_resource,
    get_team_product_limit_for_resource,
    DEFAULT_KEYS_PER_USER
    )
import logging

logger = logging.getLogger(__name__)


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

    def get_team_limits(self, team: DBTeam) -> TeamLimits:
        """
        Get all effective limits for a team.

        Args:
            team_id: ID of the team

        Returns:
            TeamLimits object containing all limits for the team
        """
        user_ids = self.db.execute(select(DBUser.id).filter(DBUser.team_id == team.id)).scalars().all()
        team_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id
            )
        ).all()
        user_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.USER,
                DBLimitedResource.owner_id.in_(user_ids)
            )
        ).all()
        limits = team_limits + user_limits

        limit_schemas = [
            LimitedResource.model_validate(limit) for limit in limits
        ]

        return TeamLimits(team_id=team.id, limits=limit_schemas)

    def get_user_limits(self, user: DBUser) -> TeamLimits:
        """
        Get all effective limits for a user.
        Users inherit team limits unless they have individual overrides.

        Args:
            user_id: ID of the user

        Returns:
            TeamLimits object containing effective limits for the user
        """
        # Get user-specific limits
        user_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.USER,
                DBLimitedResource.owner_id == user.id
            )
        ).all()

        # Get team limits
        team_limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == user.team_id
            )
        ).all()

        # Build effective limits: user overrides take precedence
        effective_limits = {}

        # Start with team limits
        for limit in team_limits:
            effective_limits[limit.resource] = limit

        # Override with user-specific limits
        for limit in user_limits:
            effective_limits[limit.resource] = limit

        limit_schemas = [
            LimitedResource.model_validate(limit)
            for limit in effective_limits.values()
        ]

        return TeamLimits(team_id=user.team_id, limits=limit_schemas)

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
        limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if not limit:
            raise LimitNotFoundError(f"No limit found for {owner_type} {owner_id} resource {resource_type}")

        # Data Plane limits cannot be incremented/decremented
        if limit.limit_type == LimitType.DATA_PLANE:
            raise ValueError("Cannot increment/decrement Data Plane resources")

        # Only COUNT type resources can be incremented/decremented
        if limit.unit != UnitType.COUNT:
            raise ValueError("Only COUNT type resources can be incremented/decremented")

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
        limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if not limit:
            raise LimitNotFoundError(f"No limit found for {owner_type} {owner_id} resource {resource_type}")

        # Data Plane limits cannot be incremented/decremented
        if limit.limit_type == LimitType.DATA_PLANE:
            raise ValueError("Cannot increment/decrement Data Plane resources")

        # Only COUNT type resources can be incremented/decremented
        if limit.unit != UnitType.COUNT:
            raise ValueError("Only COUNT type resources can be incremented/decremented")

        # Control Plane limits: decrement (but not below 0)
        if limit.current_value > 0:
            limit.current_value -= 1
            limit.updated_at = datetime.now(UTC)
            self.db.commit()

        return True

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
        limit = LimitedResourceBase(
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

    def _set_limit(self, limited_resource: LimitedResourceBase) -> DBLimitedResource:
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
            return existing_limit

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
            return new_limit

    def reset_team_limits(self, team: DBTeam) -> TeamLimits:
        """
        Reset all limits for a team following cascade rules.
        MANUAL -> PRODUCT -> DEFAULT based on availability.

        Args:
            team_id: ID of the team

        Returns:
            Updated team limits
        """
        team_limits = self.get_team_limits(team)
        for limit in team_limits.limits:
            self._reset_limit(limit)

        # Do a fresh pull from the db after the updates
        return self.get_team_limits(team)

    def _reset_limit(self, limit: LimitedResource) -> DBLimitedResource:
        """
        Reset a specific limit following cascade rules.
        MANUAL -> PRODUCT -> DEFAULT based on availability.

        Args:
            owner_type: OwnerType enum
            owner_id: ID of the owner
            resource_type: ResourceType enum

        Returns:
            Updated limit
        """
        if limit.owner_type == OwnerType.TEAM:
            logger.info(f"Setting resource {limit.resource} to product max for team {limit.owner_id}")
            team_id = limit.owner_id
        elif limit.owner_type == OwnerType.USER:
            logger.info(f"Trying to reset {limit.resource} limits for user {limit.owner_id}")
            user = self.db.query(DBUser).filter(DBUser.id == limit.owner_id).first()
            if not user.team_id:
                logger.warning(f"User {limit.owner_id} is a system user; cannot reset limit")
                return limit
            team_id = user.team_id
        else:
            raise ValueError(f"Unknown owner type, cannot reset limit {limit}")
        max_value = get_team_product_limit_for_resource(self.db, team_id, limit.resource)
        if not max_value:
            max_value = get_default_team_limit_for_resource(limit.resource)
            limit.limited_by = LimitSource.DEFAULT
        else:
            limit.limited_by = LimitSource.PRODUCT
        limit.max_value = max_value
        limit.set_by = "reset"

        self.db.add(limit)
        self.db.commit()
        return limit

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
        falling back to DEFAULt. Will not override MANUAL.
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
            ResourceType.KEY,
            ResourceType.VECTOR_DB,
            ResourceType.BUDGET,
            ResourceType.RPM
        ]

        # Process each resource type
        for resource_type in all_resources:
            # Skip if manual limit already exists
            existing_limit = next(
                (l for l in existing_limits.limits if l.resource == resource_type),
                None
            )
            if existing_limit and existing_limit.limited_by == LimitSource.MANUAL:
                continue

            # Determine limit type and unit based on resource
            if resource_type in [ResourceType.USER, ResourceType.KEY, ResourceType.VECTOR_DB, ResourceType.GPT_INSTANCE]:
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
            max_value = get_team_product_limit_for_resource(self.db, team.id, resource_type)
            if max_value is not None:
                limit_source = LimitSource.PRODUCT
            else:
                max_value = get_default_team_limit_for_resource(resource_type)
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
