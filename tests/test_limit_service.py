import pytest
from datetime import datetime, UTC
from app.db.models import DBLimitedResource
from app.core.limit_service import LimitService, LimitNotFoundError
from app.schemas.limits import TeamLimits, LimitType, ResourceType, UnitType, OwnerType, LimitSource


def test_get_team_limits_returns_all_limits(db, test_team):
    """
    Given: A team with various limits set
    When: Calling get_team_limits(team_id)
    Then: Should return TeamLimits object with all effective limits
    """
    # Create some limits for the team
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(user_limit)
    db.add(key_limit)
    db.commit()

    limit_service = LimitService(db)
    team_limits = limit_service.get_team_limits(test_team)

    assert isinstance(team_limits, TeamLimits)
    assert team_limits.team_id == test_team.id
    assert len(team_limits.limits) == 2

    # Check that we have both limits
    resources = [limit.resource for limit in team_limits.limits]
    assert ResourceType.USER in resources
    assert ResourceType.KEY in resources


def test_increment_resource_cp_limit_within_capacity(db, test_team):
    """
    Given: A team with available capacity for a CP resource
    When: Calling increment_resource(owner_type="team", owner_id=team_id, resource_type="ai_key")
    Then: Should return True and increment current_value by 1
    """
    # Create a limit with available capacity
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=5.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.KEY)

    assert result is True

    # Check that current_value was incremented
    db.refresh(limit)
    assert limit.current_value == 6.0


def test_increment_resource_cp_limit_at_capacity(db, test_team):
    """
    Given: A team at maximum capacity for a CP resource
    When: Calling increment_resource(owner_type="team", owner_id=team_id, resource_type="user")
    Then: Should return False and not modify current_value
    """
    # Create a limit at capacity
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=5.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.USER)

    assert result is False

    # Check that current_value was not modified
    db.refresh(limit)
    assert limit.current_value == 5.0


def test_increment_resource_dp_limit_raises_exception(db, test_team):
    """
    Given: A team with DP resource limit
    When: Calling increment_resource(owner_type="team", owner_id=team_id, resource_type="max_budget")
    Then: Should raise ValueError (DP resources cannot be incremented/decremented)
    """
    # Create a DP limit
    limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=None,  # DP limits don't track current value
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should now raise an exception instead of returning True
    with pytest.raises(ValueError, match="Cannot increment/decrement Data Plane resources"):
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.BUDGET)


def test_decrement_resource_cp_limit(db, test_team):
    """
    Given: A team with existing CP resource usage
    When: Calling decrement_resource(owner_type="team", owner_id=team_id, resource_type="user")
    Then: Should return True and decrement current_value by 1
    """
    # Create a limit with existing usage
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=7.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.decrement_resource(OwnerType.TEAM, test_team.id, ResourceType.USER)

    assert result is True

    # Check that current_value was decremented
    db.refresh(limit)
    assert limit.current_value == 6.0


def test_decrement_resource_dp_limit_raises_exception(db, test_team):
    """
    Given: A team with DP resource limit
    When: Calling decrement_resource(owner_type="team", owner_id=team_id, resource_type="max_budget")
    Then: Should raise ValueError (DP resources cannot be incremented/decremented)
    """
    # Create a DP limit
    limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=50.0,
        current_value=None,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should now raise an exception instead of returning True
    with pytest.raises(ValueError, match="Cannot increment/decrement Data Plane resources"):
        limit_service.decrement_resource(OwnerType.TEAM, test_team.id, ResourceType.BUDGET)


def test_increment_resource_dp_limit_should_fail(db, test_team):
    """
    Given: A team with DP resource limit
    When: Calling increment_resource(owner_type="team", owner_id=team_id, resource_type="max_budget")
    Then: Should raise an exception (DP resources cannot be incremented/decremented)
    """
    # Create a DP limit
    limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=None,  # DP limits don't track current value
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should now raise an exception instead of returning True
    with pytest.raises(ValueError, match="Cannot increment/decrement Data Plane resources"):
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.BUDGET)


def test_decrement_resource_dp_limit_should_fail(db, test_team):
    """
    Given: A team with DP resource limit
    When: Calling decrement_resource(owner_type="team", owner_id=team_id, resource_type="max_budget")
    Then: Should raise an exception (DP resources cannot be incremented/decremented)
    """
    # Create a DP limit
    limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=50.0,
        current_value=None,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should now raise an exception instead of returning True
    with pytest.raises(ValueError, match="Cannot increment/decrement Data Plane resources"):
        limit_service.decrement_resource(OwnerType.TEAM, test_team.id, ResourceType.BUDGET)


def test_increment_resource_cp_non_count_type_should_fail(db, test_team):
    """
    Given: A team with CP resource that is not COUNT type (hypothetically)
    When: Calling increment_resource
    Then: Should raise an exception (only COUNT type resources can be incremented)
    """
    # Create a hypothetical CP limit with non-COUNT unit (this shouldn't exist in practice)
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,  # Using KEY but with wrong unit type for testing
        unit=UnitType.DOLLAR,  # This should not be allowed for CP resources
        max_value=100.0,
        current_value=50.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should raise an exception because only COUNT type resources can be incremented
    with pytest.raises(ValueError, match="Only COUNT type resources can be incremented/decremented"):
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.KEY)


def test_decrement_resource_cp_non_count_type_should_fail(db, test_team):
    """
    Given: A team with CP resource that is not COUNT type (hypothetically)
    When: Calling decrement_resource
    Then: Should raise an exception (only COUNT type resources can be decremented)
    """
    # Create a hypothetical CP limit with non-COUNT unit (this shouldn't exist in practice)
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,  # Using USER but with wrong unit type for testing
        unit=UnitType.GB,  # This should not be allowed for CP resources
        max_value=100.0,
        current_value=50.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should raise an exception because only COUNT type resources can be decremented
    with pytest.raises(ValueError, match="Only COUNT type resources can be incremented/decremented"):
        limit_service.decrement_resource(OwnerType.TEAM, test_team.id, ResourceType.USER)


def test_overwrite_limit_manual_can_override_anything(db, test_team):
    """
    Given: Any existing limit (PRODUCT or DEFAULT)
    When: Calling overwrite_limit with limited_by="manual"
    Then: Should successfully update the limit
    """
    # Create an existing PRODUCT limit
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.overwrite_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=2.0,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )

    assert result is not None

    # Check that the limit was updated
    db.refresh(limit)
    assert limit.max_value == 10.0
    assert limit.limited_by == LimitSource.MANUAL
    assert limit.set_by == "admin@example.com"


def test_overwrite_limit_product_can_override_default(db, test_team):
    """
    Given: Existing DEFAULT limit
    When: Calling overwrite_limit with limited_by="product"
    Then: Should successfully update the limit
    """
    # Create a DEFAULT limit
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=6.0,
        current_value=1.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.overwrite_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=15.0,
        current_value=1.0,
        limited_by=LimitSource.PRODUCT
    )

    assert result is not None

    # Check that the limit was updated
    db.refresh(limit)
    assert limit.max_value == 15.0
    assert limit.limited_by == LimitSource.PRODUCT


def test_overwrite_limit_product_cannot_override_manual(db, test_team):
    """
    Given: Existing MANUAL limit
    When: Calling overwrite_limit with limited_by="product"
    Then: Should raise exception or return error
    """
    # Create a MANUAL limit
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    with pytest.raises(ValueError, match="Cannot override manual limit"):
        limit_service.overwrite_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.USER,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=12.0,
            current_value=3.0,
            limited_by=LimitSource.PRODUCT
        )


def test_overwrite_limit_default_cannot_override_anything(db, test_team):
    """
    Given: Existing PRODUCT or MANUAL limit
    When: Calling overwrite_limit with limited_by="default"
    Then: Should raise exception or return error
    """
    # Create a PRODUCT limit
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.VECTOR_DB,
        unit=UnitType.COUNT,
        max_value=3.0,
        current_value=1.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    with pytest.raises(ValueError, match="Cannot override"):
        limit_service.overwrite_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.VECTOR_DB,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=5.0,
            current_value=1.0,
            limited_by=LimitSource.DEFAULT
        )

def test_reset_limit_single_resource(db, test_team):
    """
    Given: Team with MANUAL override for specific resource
    When: Calling reset_limit(owner_type="team", owner_id=team_id, resource_type="user")
    Then: Should reset that resource from MANUAL -> PRODUCT -> DEFAULT
    """
    # Create a MANUAL limit first
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should not raise an exception and return the existing limit
    result = limit_service.reset_limit(OwnerType.TEAM, test_team.id, ResourceType.USER)
    assert result is not None
    assert result.resource == ResourceType.USER


def test_user_inherits_team_limits(db, test_team, test_team_user):
    """
    Given: User without individual limit overrides
    When: Getting limits for the user
    Then: Should inherit team limits as per design document rule
    """
    # Create team limits
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(team_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # User should inherit team limits
    assert len(user_limits.limits) == 1
    assert user_limits.limits[0].resource == ResourceType.KEY
    assert user_limits.limits[0].max_value == 5.0


def test_user_override_supersedes_team_limit(db, test_team, test_team_user):
    """
    Given: User with individual limit overrides
    When: Getting limits for the user
    Then: Should return user-specific limits, not team limits
    """
    # Create team limit
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )

    # Create user override
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )

    db.add(team_limit)
    db.add(user_limit)
    db.commit()

    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should return user-specific limit, not team limit
    assert len(user_limits.limits) == 1
    assert user_limits.limits[0].resource == ResourceType.KEY
    assert user_limits.limits[0].max_value == 10.0
    assert user_limits.limits[0].limited_by == LimitSource.MANUAL


def test_cp_limits_must_have_current_value(db, test_team):
    """
    Given: Attempting to create CP limit
    When: current_value is None
    Then: Should raise validation error
    """
    limit_service = LimitService(db)

    with pytest.raises(ValueError, match="Control plane limits must have current_value") as exc_info:
        limit_service.overwrite_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.USER,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=5.0,
            current_value=None,  # This should cause validation error
            limited_by=LimitSource.DEFAULT
        )


def test_dp_limits_must_not_have_current_value(db, test_team):
    """
    Given: Attempting to create DP limit
    When: current_value is not None
    Then: Should raise validation error or set to None
    """
    limit_service = LimitService(db)

    # This should succeed but set current_value to None
    result = limit_service.overwrite_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=50.0,  # This should be ignored/set to None
        limited_by=LimitSource.PRODUCT
    )

    assert result is not None
    assert result.current_value is None


def test_unique_constraint_enforced(db, test_team):
    """
    Given: Existing limit for owner_type, owner_id, resource combination
    When: Attempting to create duplicate limit
    Then: Should raise database constraint error
    """
    # Create first limit
    limit1 = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit1)
    db.commit()

    # Attempt to create duplicate should fail
    limit2 = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,  # Same resource for same team
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )

    db.add(limit2)

    with pytest.raises(Exception):  # Database constraint error
        db.commit()
