import pytest
from datetime import datetime, UTC
from app.db.models import DBLimitedResource, DBTeamProduct
from app.core.limit_service import LimitService, LimitNotFoundError
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource
from app.core.limit_service import DEFAULT_USER_COUNT, DEFAULT_KEYS_PER_USER


def test_get_team_limits_returns_all_limits(db, test_team):
    """
    Given: A team with various limits set
    When: Calling get_team_limits(team_id)
    Then: Should return a list of LimitedResource objects with all effective limits
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

    assert isinstance(team_limits, list)
    assert len(team_limits) == 2

    # Check that we have both limits
    resources = [limit.resource for limit in team_limits]
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
    result = limit_service.set_limit(
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
    result = limit_service.set_limit(
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
        limit_service.set_limit(
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
        limit_service.set_limit(
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
    Given: Team with MANUAL override for specific resource and no associated products
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
    assert result.max_value == DEFAULT_USER_COUNT
    assert result.limited_by == LimitSource.DEFAULT

def test_reset_limit_to_product_single_resource(db, test_team, test_product):
    """
    Given: Team with MANUAL override for specific resource and associated products
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

    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id,
        created_at=datetime.now(UTC)
    )
    db.add(team_product)

    db.commit()

    limit_service = LimitService(db)

    # This should not raise an exception and return the new limit value
    result = limit_service.reset_limit(OwnerType.TEAM, test_team.id, ResourceType.USER)
    assert result is not None
    assert result.resource == ResourceType.USER
    assert result.max_value == test_product.user_count
    assert result.limited_by == LimitSource.PRODUCT

def test_reset_user_limit_uses_team(db, test_team_user, test_team, test_product):
    """
    GIVEN: User in a team which has products
    WHEN: Limits owned by that user are reset
    THEN: The team product values are used
    """
    # Create a MANUAL limit first
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.reset_limit(limit.owner_type, limit.owner_id, limit.resource)
    assert result.max_value == test_product.service_key_count

def test_reset_team_limits_handles_limited_resource_mapping_error(db, test_team):
    """
    GIVEN: A team with MANUAL limits that need to be reset
    WHEN: Calling reset_team_limits with the team
    THEN: Should successfully reset limits without mapping errors
    """
    # Create a MANUAL limit first
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
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

    # This should not raise "Class 'app.schemas.limits.LimitedResource' is not mapped" error
    result = limit_service.reset_team_limits(test_team)

    assert isinstance(result, list)
    assert len(result) > 0

    # Check that the limit was reset (should be DEFAULT or PRODUCT, not MANUAL)
    key_limit = next((l for l in result if l.resource == ResourceType.KEY), None)
    assert key_limit is not None
    assert key_limit.limited_by in [LimitSource.DEFAULT, LimitSource.PRODUCT]
    assert key_limit.set_by == "reset"


def test_reset_system_user_limit_does_nothing(db, test_user):
    """
    GIVEN: User in a team which has products
    WHEN: Limits owned by that user are reset
    THEN: The team product values are used
    """
    # Create a MANUAL limit first
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)
    result = limit_service.reset_limit(limit.owner_type, limit.owner_id, limit.resource)
    assert result.max_value == limit.max_value

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
    assert len(user_limits) == 1
    assert user_limits[0].resource == ResourceType.KEY
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
    assert len(user_limits) == 1
    assert user_limits[0].resource == ResourceType.KEY
    assert user_limits[0].max_value == 10.0
    assert user_limits[0].limited_by == LimitSource.MANUAL

def test_user_limits_not_included_in_team_limits(db, test_team, test_team_user, test_product):
    """
    GIVEN: A team with a user with an override
    WHEN: We call get_team_limits
    THEN: The user override should NOT be included (team limits are independent)
    """
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

    db.add(user_limit)
    db.commit()
    db.refresh(test_team)

    limit_service = LimitService(db)
    limit_list = limit_service.get_team_limits(test_team)
    # Should have no limits since there are no team or system limits
    assert len(limit_list) == 0


def test_cp_limits_must_have_current_value(db, test_team):
    """
    Given: Attempting to create CP limit
    When: current_value is None
    Then: Should raise validation error
    """
    limit_service = LimitService(db)

    with pytest.raises(ValueError, match="Control plane limits must have current_value") as exc_info:
        limit_service.set_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.USER,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=5.0,
            current_value=None,  # This should cause validation error
            limited_by=LimitSource.DEFAULT
        )

def test_dp_limits_can_have_current_value(db, test_team):
    """
    Given: Attempting to create DP limit
    When: current_value is not None
    Then: current_value should be set
    """
    limit_service = LimitService(db)

    # This should succeed but set current_value to None
    result = limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=50.0,
        limited_by=LimitSource.PRODUCT
    )

    assert result is not None
    assert result.current_value == 50.0

def test_dp_limits_can_not_have_current_value(db, test_team):
    """
    Given: Attempting to create DP limit
    When: current_value is None
    Then: current_value should be None
    """
    limit_service = LimitService(db)

    # This should succeed but set current_value to None
    result = limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=None,
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


def test_set_team_limits_creates_default_limits(db, test_team):
    """
    Given: A team with no existing limits
    When: Calling set_team_limits(team)
    Then: Should create all default limits for the team
    """
    limit_service = LimitService(db)

    # Initially no limits should exist
    initial_limits = limit_service.get_team_limits(test_team)
    assert len(initial_limits) == 0

    # Set team limits
    limit_service.set_team_limits(test_team)

    # Check that all default limits were created
    team_limits = limit_service.get_team_limits(test_team)

    # Should have all the supported resource types
    expected_resources = {
        ResourceType.USER,
        ResourceType.KEY,
        ResourceType.VECTOR_DB,
        ResourceType.BUDGET,
        ResourceType.RPM
    }

    actual_resources = {limit.resource for limit in team_limits}
    assert actual_resources == expected_resources

    # All limits should be DEFAULT source
    for limit in team_limits:
        assert limit.limited_by == LimitSource.DEFAULT
        assert limit.owner_type == OwnerType.TEAM
        assert limit.owner_id == test_team.id


def test_set_team_limits_with_products_uses_product_limits(db, test_team, test_product):
    """
    Given: A team with associated products
    When: Calling set_team_limits(team)
    Then: Should use product limits where available, falling back to defaults
    """
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id,
        created_at=datetime.now(UTC)
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    limit_service.set_team_limits(test_team)

    team_limits = limit_service.get_team_limits(test_team)

    # Find the USER limit and check it uses product value
    user_limit = next((l for l in team_limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == test_product.user_count
    assert user_limit.limited_by == LimitSource.PRODUCT

    # Find the KEY limit and check it uses product value
    key_limit = next((l for l in team_limits if l.resource == ResourceType.KEY), None)
    assert key_limit is not None
    assert key_limit.max_value == test_product.service_key_count
    assert key_limit.limited_by == LimitSource.PRODUCT


def test_set_team_limits_preserves_manual_limits(db, test_team):
    """
    Given: A team with existing manual limits
    When: Calling set_team_limits(team)
    Then: Should not override manual limits, only set missing ones
    """
    # Create a manual limit first
    manual_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(manual_limit)
    db.commit()

    limit_service = LimitService(db)
    limit_service.set_team_limits(test_team)

    team_limits = limit_service.get_team_limits(test_team)

    # Check that the manual limit was preserved
    user_limit = next((l for l in team_limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == 10.0
    assert user_limit.limited_by == LimitSource.MANUAL
    assert user_limit.set_by == "admin@example.com"

    # Check that other limits were still created
    assert len(team_limits) >= 4  # Should have all other supported resources


def test_set_team_limits_updates_product_limits(db, test_team, test_product):
    """
    Given: A team with existing default limits and new product association
    When: Calling set_team_limits(team)
    Then: Should update limits to use product values where available
    """
    # Create default limits first
    default_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=DEFAULT_KEYS_PER_USER,  # Default value
        current_value=1.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(default_limit)
    db.commit()

    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id,
        created_at=datetime.now(UTC)
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    limit_service.set_team_limits(test_team)

    team_limits = limit_service.get_team_limits(test_team)

    # Check that the limit was updated to use product value
    key_limit = next((l for l in team_limits if l.resource == ResourceType.KEY), None)
    assert key_limit is not None
    assert key_limit.max_value == test_product.service_key_count
    assert key_limit.limited_by == LimitSource.PRODUCT


def test_set_team_limits_preserves_current_value_when_updating(db, test_team, test_product):
    """
    Given: A team with existing limits that have current_value set
    When: Calling set_team_limits(team) after adding a product
    Then: Should preserve the existing current_value while updating max_value and source
    """
    # Create a default limit with existing usage
    existing_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=DEFAULT_USER_COUNT,
        current_value=2.0,  # Team already has 2 users
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(existing_limit)
    db.commit()

    # Create team-product association (product has higher user count)
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id,
        created_at=datetime.now(UTC)
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    limit_service.set_team_limits(test_team)

    team_limits = limit_service.get_team_limits(test_team)

    # Check that current_value was preserved while max_value and source were updated
    user_limit = next((l for l in team_limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == test_product.user_count  # Updated to product value
    assert user_limit.current_value == 2.0  # Preserved existing value
    assert user_limit.limited_by == LimitSource.PRODUCT  # Updated source


def test_set_current_value_for_data_plane_budget_limit(db, test_team):
    """
    Given: A DATA_PLANE BUDGET limit with initial current_value
    When: set_current_value is called with a new spend value
    Then: current_value is updated to the new value and committed
    """
    # Create a DATA_PLANE BUDGET limit
    budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=25.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(budget_limit)
    db.commit()

    limit_service = LimitService(db)
    from app.schemas.limits import LimitedResource
    limit_schema = LimitedResource.model_validate(budget_limit)

    # Update the current value to new spend
    limit_service.set_current_value(limit_schema, 45.75)

    # Verify the value was updated
    db.refresh(budget_limit)
    assert budget_limit.current_value == 45.75


def test_set_current_value_for_control_plane_count_at_zero(db, test_team):
    """
    Given: A CONTROL_PLANE COUNT limit with current_value = 0.0
    When: set_current_value is called with an actual count
    Then: current_value is set to the new value (allowed for initial set)
    """
    # Create a CONTROL_PLANE COUNT limit at zero
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(user_limit)
    db.commit()

    limit_service = LimitService(db)
    from app.schemas.limits import LimitedResource
    limit_schema = LimitedResource.model_validate(user_limit)

    # Set initial count
    limit_service.set_current_value(limit_schema, 3.0)

    # Verify the value was set
    db.refresh(user_limit)
    assert user_limit.current_value == 3.0


def test_set_current_value_control_plane_count_already_set_raises_error(db, test_team):
    """
    Given: A CONTROL_PLANE COUNT limit with current_value > 0.0
    When: set_current_value is called
    Then: ValueError is raised with appropriate message
    """
    # Create a CONTROL_PLANE COUNT limit with non-zero value
    key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=5.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(key_limit)
    db.commit()

    limit_service = LimitService(db)
    from app.schemas.limits import LimitedResource
    limit_schema = LimitedResource.model_validate(key_limit)

    # Attempting to set value again should raise error
    with pytest.raises(ValueError, match="Control Plane counters must be incremented or decremented"):
        limit_service.set_current_value(limit_schema, 7.0)


def test_set_current_value_updates_correct_attribute(db, test_team):
    """
    Given: A DATA_PLANE BUDGET limit
    When: set_current_value is called
    Then: The database field 'current_value' is correctly updated (verifying typo fix)
    """
    # Create a limit
    budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(budget_limit)
    db.commit()

    limit_service = LimitService(db)
    from app.schemas.limits import LimitedResource
    limit_schema = LimitedResource.model_validate(budget_limit)

    # Update current value
    limit_service.set_current_value(limit_schema, 123.45)

    # Refresh and verify the correct attribute was updated
    db.refresh(budget_limit)
    assert budget_limit.current_value == 123.45
    # Verify the object has the correct attribute name
    assert hasattr(budget_limit, 'current_value')


def test_reset_limit_uses_updated_system_default_values(db, test_team_user, test_team):
    """
    Given: A team with existing limits all set to default values, and the default limit for users has been modified
    When: Reset limit is called for the user limit
    Then: The value should match the _new_ default

    This test demonstrates the bug where reset_limit uses hardcoded constants instead of
    the current SYSTEM limit values from the database.
    """
    # Create a MANUAL user limit first (simulating an existing limit)
    manual_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(manual_limit)

    # Create a SYSTEM limit with a NEW default value (simulating updated default)
    NEW_DEFAULT_USER_COUNT = 5.0  # Different from the constant DEFAULT_USER_COUNT = 1
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=NEW_DEFAULT_USER_COUNT,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT,
        set_by="system",
        created_at=datetime.now(UTC)
    )
    db.add(system_limit)
    db.commit()

    limit_service = LimitService(db)

    # Reset the user limit - this should use the NEW system default value
    result = limit_service.reset_limit(
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        resource_type=ResourceType.USER
    )

    # The result should use the NEW system default, not the hardcoded constant
    assert result.max_value == NEW_DEFAULT_USER_COUNT, f"Expected {NEW_DEFAULT_USER_COUNT}, got {result.max_value}"
    assert result.limited_by == LimitSource.DEFAULT
    assert result.set_by == "reset"

def test_get_team_limits_returns_team_limits_with_correct_owner_info(db, test_team):
    """
    Given: A system limit exists for a resource type, but no team limit exists
    When: Calling get_team_limits(team)
    Then: Should return the system limit with owner_type=TEAM and owner_id=team.id

    This test reproduces the exact bug described: when there's a system limit
    but no team limit, get_team_limits returns the system limit with system
    owner info, but the admin needs to see it as a team limit so they can edit it.
    """
    # Create a system limit for KEY resource type
    system_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT,
        set_by="system",
        created_at=datetime.now(UTC)
    )
    db.add(system_limit)
    db.commit()

    # No team limit exists for this resource type

    limit_service = LimitService(db)
    team_limits = limit_service.get_team_limits(test_team)

    # Should return exactly one limit (the system limit)
    assert len(team_limits) == 1

    # The bug: this should be returned as a TEAM limit, not SYSTEM limit
    # so the admin can edit it for this specific team
    limit = team_limits[0]
    assert limit.owner_type == OwnerType.TEAM, f"Expected TEAM, got {limit.owner_type}"
    assert limit.owner_id == test_team.id, f"Expected team_id {test_team.id}, got {limit.owner_id}"
    assert limit.resource == ResourceType.KEY
    assert limit.max_value == 10.0
    assert limit.limited_by == LimitSource.DEFAULT
