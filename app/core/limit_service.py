from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import Optional, List
from datetime import datetime, UTC
from app.db.models import DBLimitedResource, DBUser, DBTeam
from app.schemas.limits import TeamLimits, LimitedResource as LimitedResourceSchema, LimitType, ResourceType, UnitType, OwnerType, LimitSource
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
        limits = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id
            )
        ).all()

        limit_schemas = [
            LimitedResourceSchema.model_validate(limit) for limit in limits
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
            LimitedResourceSchema.model_validate(limit)
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

    def overwrite_limit(
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
        """
        Create or update a limit following source hierarchy rules.

        Source hierarchy: MANUAL > PRODUCT > DEFAULT
        - MANUAL can override anything
        - PRODUCT can override DEFAULT
        - DEFAULT cannot override anything

        Args:
            owner_type: OwnerType enum
            owner_id: ID of the owner
            resource_type: ResourceType enum
            limit_type: LimitType enum
            unit: UnitType enum
            max_value: Maximum allowed value
            current_value: Current value (required for CP, ignored for DP)
            limited_by: LimitSource enum
            set_by: Who set the limit (required for manual limits)

        Returns:
            The created or updated limit

        Raises:
            ValueError: If validation fails or hierarchy rules are violated
        """
        # Validate inputs
        if limit_type == LimitType.CONTROL_PLANE and current_value is None:
            raise ValueError("Control plane limits must have current_value")

        if limit_type == LimitType.DATA_PLANE:
            current_value = None  # Force None for DP limits

        if limited_by == LimitSource.MANUAL and not set_by:
            raise ValueError("Manual limits must specify set_by")

        # Check if limit already exists
        existing_limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if existing_limit:
            # Apply hierarchy rules
            if existing_limit.limited_by == LimitSource.MANUAL and limited_by != LimitSource.MANUAL:
                raise ValueError("Cannot override manual limit with non-manual limit")

            if existing_limit.limited_by == LimitSource.PRODUCT and limited_by == LimitSource.DEFAULT:
                raise ValueError("Cannot override product limit with default limit")

            # Update existing limit (enums are already the correct type)
            existing_limit.limit_type = limit_type
            existing_limit.unit = unit
            existing_limit.max_value = max_value
            existing_limit.current_value = current_value
            existing_limit.limited_by = limited_by
            existing_limit.set_by = set_by if limited_by == LimitSource.MANUAL else None
            existing_limit.updated_at = datetime.now(UTC)

            self.db.commit()
            return existing_limit

        else:
            # Create new limit (enums are already the correct type)
            new_limit = DBLimitedResource(
                limit_type=limit_type,
                resource=resource_type,
                unit=unit,
                max_value=max_value,
                current_value=current_value,
                owner_type=owner_type,
                owner_id=owner_id,
                limited_by=limited_by,
                set_by=set_by if limited_by == LimitSource.MANUAL else None,
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
        # For now, return current limits (placeholder implementation)
        # Full implementation would involve product lookups and default fallbacks
        # TODO - Full implementation
        return self.get_team_limits(team)

    def reset_limit(self, owner_type: OwnerType, owner_id: int, resource_type: ResourceType) -> DBLimitedResource:
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
        # For now, return existing limit (placeholder implementation)
        # Full implementation would involve product lookups and default fallbacks
        limit = self.db.query(DBLimitedResource).filter(
            and_(
                DBLimitedResource.owner_type == owner_type,
                DBLimitedResource.owner_id == owner_id,
                DBLimitedResource.resource == resource_type
            )
        ).first()

        if not limit:
            raise LimitNotFoundError(f"No limit found for {owner_type} {owner_id} resource {resource_type}")

        return limit
