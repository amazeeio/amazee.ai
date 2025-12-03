import pytest
from datetime import datetime, UTC
from app.db.models import DBLimitedResource
from app.core.limit_service import LimitService
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


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
    service_key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(user_limit)
    db.add(service_key_limit)
    db.commit()

    limit_service = LimitService(db)
    team_limits = limit_service.get_team_limits(test_team)

    assert isinstance(team_limits, list)
    assert len(team_limits) == 2

    # Check that we have both limits
    resources = [limit.resource for limit in team_limits]
    assert ResourceType.USER in resources
    assert ResourceType.SERVICE_KEY in resources


def test_increment_resource_cp_limit_within_capacity(db, test_team):
    """
    Given: A team with available capacity for a CP resource
    When: Calling increment_resource(owner_type="team", owner_id=team_id, resource_type="ai_key")
    Then: Should return True and increment current_value by 1
    """
    # Create a limit with available capacity
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
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
    result = limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.SERVICE_KEY)

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
        resource=ResourceType.SERVICE_KEY,
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
        resource_type=ResourceType.SERVICE_KEY,
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


def test_overwrite_limit_default_can_override_product(db, test_team):
    """
    Given: Existing PRODUCT limit
    When: Calling set_limit with limited_by="default"
    Then: Should successfully update the limit (needed when products are removed)
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

    # This should now succeed - we allow product to default transitions
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

    # Verify the limit was updated
    updated_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == test_team.id,
        DBLimitedResource.resource == ResourceType.VECTOR_DB
    ).first()

    assert updated_limit is not None
    assert updated_limit.limited_by == LimitSource.DEFAULT
    assert updated_limit.max_value == 5.0


def test_overwrite_limit_default_cannot_override_manual(db, test_team):
    """
    Given: Existing MANUAL limit
    When: Calling set_limit with limited_by="default"
    Then: Should raise exception (manual limits should still be protected)
    """
    # Create a MANUAL limit
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.VECTOR_DB,
        unit=UnitType.COUNT,
        max_value=3.0,
        current_value=1.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    limit_service = LimitService(db)

    # This should still raise an error - manual limits are protected
    with pytest.raises(ValueError, match="Cannot override manual limit"):
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


def test_set_limit_allows_product_to_default_transition(db, test_team):
    """
    Test that set_limit allows transitioning from PRODUCT to DEFAULT limit source.

    GIVEN: A team with a PRODUCT limit
    WHEN: Setting a DEFAULT limit for the same resource
    THEN: The transition should be allowed (this is needed when products are removed)
    """
    # Create a product limit first
    product_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(product_limit)
    db.commit()

    limit_service = LimitService(db)

    # This should NOT raise an error - we need to allow product to default transitions
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        limited_by=LimitSource.DEFAULT
    )

    # Verify the limit was updated
    updated_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == test_team.id,
        DBLimitedResource.resource == ResourceType.USER
    ).first()

    assert updated_limit is not None
    assert updated_limit.limited_by == LimitSource.DEFAULT
    assert updated_limit.max_value == 5.0
    assert updated_limit.current_value == 2.0
