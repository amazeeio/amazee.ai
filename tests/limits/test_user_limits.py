import pytest
from datetime import datetime, UTC
from fastapi import HTTPException
from app.db.models import DBUser, DBTeam, DBLimitedResource
from app.core.limit_service import LimitService, DEFAULT_USER_COUNT
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource


def test_add_user_within_default_limit(db, test_team):
    """Test adding a user when within the default user limit"""
    db.commit()

    # Test that check_team_user_limit doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_team_user_limit(test_team.id)


def test_add_user_exceeding_default_limit(db, test_team):
    """Test adding a user when it would exceed the default user limit"""
    db.commit()

    # Create and add users up to the limit
    for i in range(DEFAULT_USER_COUNT):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC),
        )
        db.add(user)
    db.commit()

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert (
        f"Team has reached the maximum user limit of {DEFAULT_USER_COUNT} users"
        in str(exc_info.value.detail)
    )


def test_check_team_user_limit_with_limit_service(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service
    WHEN: Checking team user limits
    THEN: The limit service is used first and succeeds
    """
    # Set up a limit in the new service
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        limited_by=LimitSource.DEFAULT,
    )

    # Test that check_team_user_limit doesn't raise an exception
    limit_service.check_team_user_limit(test_team.id)


def test_check_team_user_limit_with_limit_service_at_capacity(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service at capacity
    WHEN: Checking team user limits
    THEN: The limit service is used first and raises an exception
    """
    # The stored count is corrected against the real members before the check,
    # so the team needs as many members as the cap it sits at.
    for i in range(3):
        db.add(
            DBUser(
                email=f"at-capacity{i}@example.com",
                hashed_password="hashed_password",
                is_active=True,
                is_admin=False,
                role="read_only",
                team_id=test_team.id,
                created_at=datetime.now(UTC),
            )
        )
    db.commit()

    # Set up a limit in the new service at capacity
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=3.0,
        current_value=3.0,  # At capacity
        limited_by=LimitSource.DEFAULT,
    )

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum user limit" in str(exc_info.value.detail)


def test_check_team_user_limit_fallback_creates_limit(db, test_team):
    """
    GIVEN: A team with no limits in the new service
    WHEN: Checking team user limits
    THEN: The fallback code runs and creates a new limit in the service
    """
    db.commit()

    # Verify no limit exists in the service initially
    limit_service = LimitService(db)
    try:
        limit_service.increment_resource(
            OwnerType.TEAM, test_team.id, ResourceType.USER
        )
        assert False, "Should have raised LimitNotFoundError"
    except Exception:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_team_user_limit(test_team.id)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a USER limit now
    user_limits = [
        limit for limit in team_limits if limit.resource == ResourceType.USER
    ]
    assert len(user_limits) == 1
    user_limit = user_limits[0]
    assert user_limit.max_value == DEFAULT_USER_COUNT
    assert user_limit.current_value == 1.0  # Should be 1 after the increment


def test_user_inherits_team_limits(db, test_team, test_team_user):
    """
    Given: User without individual limit overrides
    When: Getting limits for the user
    Then: Should inherit team limits as per design document rule
    """
    # Create team limits
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(team_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # User should inherit team limits
    assert len(user_limits) == 1
    assert user_limits[0].resource == ResourceType.SERVICE_KEY
    assert user_limits[0].max_value == 5.0


def test_user_override_supersedes_team_limit(db, test_team, test_team_user):
    """
    Given: User with individual limit overrides
    When: Getting limits for the user
    Then: Should return user-specific limits, not team limits
    """
    # Create team limit
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )

    # Create user override
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC),
    )

    db.add(team_limit)
    db.add(user_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should return user-specific limit, not team limit
    assert len(user_limits) == 1
    assert user_limits[0].resource == ResourceType.SERVICE_KEY
    assert user_limits[0].max_value == 10.0
    assert user_limits[0].limited_by == LimitSource.MANUAL


def test_user_limits_not_included_in_team_limits(db, test_team, test_team_user):
    """
    GIVEN: A team with a user with an override
    WHEN: We call get_team_limits
    THEN: The user override should NOT be included (team limits are independent)
    """
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC),
    )

    db.add(user_limit)
    db.commit()
    db.refresh(test_team)

    limit_service = LimitService(db)
    limit_list = limit_service.get_team_limits(test_team)
    # Should have no limits since there are no team or system limits
    assert len(limit_list) == 0
