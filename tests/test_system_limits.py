import pytest
from sqlalchemy.orm import Session
from app.db.models import DBLimitedResource, DBTeam, DBUser
from app.core.limit_service import LimitService, LimitNotFoundError
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


def test_system_limits_cannot_be_reset(db: Session):
    """
    Given: A SYSTEM limit exists in the database
    When: Attempting to reset the SYSTEM limit
    Then: Should raise an exception as SYSTEM limits cannot be reset
    """
    # Create a SYSTEM limit
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=1.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT
    )
    db.add(system_limit)
    db.commit()

    limit_service = LimitService(db)

    # Attempt to reset SYSTEM limit should raise an exception
    with pytest.raises(ValueError, match="Cannot reset SYSTEM limits"):
        limit_service.reset_limit(
            owner_type=OwnerType.SYSTEM,
            owner_id=0,
            resource_type=ResourceType.USER
        )


def test_system_limits_only_allow_default_or_manual_source(db: Session):
    """
    Given: Attempting to create a SYSTEM limit
    When: Using an invalid LimitSource (not DEFAULT or MANUAL)
    Then: Should raise an exception
    """
    limit_service = LimitService(db)

    # Attempt to create SYSTEM limit with PRODUCT source should fail
    with pytest.raises(ValueError, match="SYSTEM limits can only have DEFAULT or MANUAL source"):
        limit_service.set_limit(
            owner_type=OwnerType.SYSTEM,
            owner_id=0,
            resource_type=ResourceType.USER,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=1.0,
            current_value=0.0,
            limited_by=LimitSource.PRODUCT
        )


def test_system_limits_allow_default_source(db: Session):
    """
    Given: Creating a SYSTEM limit
    When: Using DEFAULT as the LimitSource
    Then: Should succeed
    """
    limit_service = LimitService(db)

    result = limit_service.set_limit(
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=1.0,
        current_value=0.0,
        limited_by=LimitSource.DEFAULT
    )

    assert result.owner_type == OwnerType.SYSTEM
    assert result.owner_id == 0
    assert result.limited_by == LimitSource.DEFAULT


def test_system_limits_allow_manual_source(db: Session):
    """
    Given: Creating a SYSTEM limit
    When: Using MANUAL as the LimitSource with set_by
    Then: Should succeed
    """
    limit_service = LimitService(db)

    result = limit_service.set_limit(
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=2.0,
        current_value=0.0,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )

    assert result.owner_type == OwnerType.SYSTEM
    assert result.owner_id == 0
    assert result.limited_by == LimitSource.MANUAL
    assert result.set_by == "admin@example.com"


def test_inheritance_user_team_system(db: Session, test_team, test_team_user):
    """
    Given: A user with no individual limits, team with no limits, and system defaults
    When: Getting user limits
    Then: Should inherit from system defaults
    """
    # Create system default limit
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT
    )
    db.add(system_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should find the system default limit
    user_limit = next((l for l in user_limits.limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == 5.0
    assert user_limit.owner_type == OwnerType.SYSTEM


def test_inheritance_team_overrides_system(db: Session, test_team, test_team_user):
    """
    Given: A user with no individual limits, team with limits, and system defaults
    When: Getting user limits
    Then: Should inherit from team limits (not system)
    """
    # Create system default limit
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT
    )
    db.add(system_limit)

    # Create team limit that overrides system
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )
    db.add(team_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should find the team limit (not system)
    user_limit = next((l for l in user_limits.limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == 10.0
    assert user_limit.owner_type == OwnerType.TEAM


def test_inheritance_user_overrides_team_and_system(db: Session, test_team, test_team_user):
    """
    Given: A user with individual limits, team with limits, and system defaults
    When: Getting user limits
    Then: Should use user limits (not team or system)
    """
    # Create system default limit
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT
    )
    db.add(system_limit)

    # Create team limit
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )
    db.add(team_limit)

    # Create user limit that overrides both
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=15.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )
    db.add(user_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should find the user limit (not team or system)
    found_limit = next((l for l in user_limits.limits if l.resource == ResourceType.USER), None)
    assert found_limit is not None
    assert found_limit.max_value == 15.0
    assert found_limit.owner_type == OwnerType.USER


def test_get_system_limits(db: Session):
    """
    Given: Multiple system limits exist
    When: Getting system limits
    Then: Should return all system limits
    """
    # Create multiple system limits
    system_limits = [
        DBLimitedResource(
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.USER,
            unit=UnitType.COUNT,
            max_value=1.0,
            current_value=0.0,
            owner_type=OwnerType.SYSTEM,
            owner_id=0,
            limited_by=LimitSource.DEFAULT
        ),
        DBLimitedResource(
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.KEY,
            unit=UnitType.COUNT,
            max_value=6.0,
            current_value=0.0,
            owner_type=OwnerType.SYSTEM,
            owner_id=0,
            limited_by=LimitSource.DEFAULT
        )
    ]
    for limit in system_limits:
        db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    system_limits_result = limit_service.get_system_limits()

    assert len(system_limits_result.limits) == 2
    assert all(limit.owner_type == OwnerType.SYSTEM for limit in system_limits_result.limits)
    assert all(limit.owner_id == 0 for limit in system_limits_result.limits)
