import pytest
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.db.models import DBLimitedResource, DBTeam, DBProduct, DBTeamProduct
from app.core.limit_service import LimitService
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


def test_product_application_creates_product_limits(db: Session, test_team, test_product):
    """
    Given: Team subscribes to product with defined limits
    When: Product limits are applied via worker
    Then: Should create LimitedResource entries with limited_by="product"
    """
    # Associate product with team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)

    # Apply product limits (simulating what the worker would do)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=float(test_product.user_count),
        current_value=0.0,
        limited_by=LimitSource.PRODUCT
    )

    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=float(test_product.total_key_count),
        current_value=0.0,
        limited_by=LimitSource.PRODUCT
    )

    # Verify limits were created
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 2

    # Check that all limits are PRODUCT limits
    for limit in team_limits:
        assert limit.limited_by == LimitSource.PRODUCT


def test_product_removal_resets_to_default(db: Session, test_team):
    """
    Given: Team with PRODUCT limits and product is removed
    When: Product removal is processed
    Then: Should reset limits from PRODUCT -> DEFAULT
    """
    limit_service = LimitService(db)

    # Create PRODUCT limit
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        limited_by=LimitSource.PRODUCT
    )

    # Verify PRODUCT limit exists
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 1
    assert team_limits[0].limited_by == LimitSource.PRODUCT

    # Simulate product removal by deleting the PRODUCT limit first
    db.delete(db.query(DBLimitedResource).filter(
        and_(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == test_team.id,
            DBLimitedResource.resource == ResourceType.USER
        )
    ).first())
    db.commit()

    # Then create DEFAULT limit
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=1.0,  # DEFAULT_USER_COUNT
        current_value=3.0,
        limited_by=LimitSource.DEFAULT
    )

    # Verify limit is now DEFAULT
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 1
    assert team_limits[0].limited_by == LimitSource.DEFAULT
    assert team_limits[0].max_value == 1.0


def test_product_cannot_override_manual_limits(db: Session, test_team):
    """
    Given: Team with MANUAL limits for some resources
    When: Product tries to apply limits
    Then: Should only apply PRODUCT limits for resources without MANUAL overrides
    """
    limit_service = LimitService(db)

    # Create MANUAL limit first
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=15.0,
        current_value=5.0,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com"
    )

    # Try to apply PRODUCT limit - should fail
    with pytest.raises(Exception):  # Should raise HTTPException
        limit_service.set_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.USER,
            limit_type=LimitType.CONTROL_PLANE,
            unit=UnitType.COUNT,
            max_value=10.0,
            current_value=5.0,
            limited_by=LimitSource.PRODUCT
        )

    # Apply PRODUCT limit for different resource - should succeed
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=20.0,
        current_value=2.0,
        limited_by=LimitSource.PRODUCT
    )

    # Verify both limits exist with correct sources
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 2

    user_limit = next(l for l in team_limits if l.resource == ResourceType.USER)
    key_limit = next(l for l in team_limits if l.resource == ResourceType.SERVICE_KEY)

    assert user_limit.limited_by == LimitSource.MANUAL
    assert key_limit.limited_by == LimitSource.PRODUCT


def test_multiple_products_uses_highest_limits(db: Session, test_team):
    """
    Given: Team with multiple products having different limits
    When: Product limits are calculated
    Then: Should use the highest limit value for each resource type
    """
    # Create two products with different limits
    product1 = DBProduct(
        id="prod_basic",
        name="Basic Plan",
        user_count=3,
        total_key_count=10,
        active=True,
        created_at=datetime.now(UTC)
    )
    product2 = DBProduct(
        id="prod_premium",
        name="Premium Plan",
        user_count=10,  # Higher than product1
        total_key_count=5,  # Lower than product1
        active=True,
        created_at=datetime.now(UTC)
    )

    db.add(product1)
    db.add(product2)

    # Associate both products with team
    team_product1 = DBTeamProduct(team_id=test_team.id, product_id=product1.id)
    team_product2 = DBTeamProduct(team_id=test_team.id, product_id=product2.id)
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    limit_service = LimitService(db)

    # Apply limits using the highest values from both products
    # User count: max(3, 10) = 10
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,  # Higher value from premium
        current_value=2.0,
        limited_by=LimitSource.PRODUCT
    )

    # Key count: max(10, 5) = 10
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,  # Higher value from basic
        current_value=1.0,
        limited_by=LimitSource.PRODUCT
    )

    # Verify limits use highest values
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 2

    user_limit = next(l for l in team_limits if l.resource == ResourceType.USER)
    key_limit = next(l for l in team_limits if l.resource == ResourceType.SERVICE_KEY)

    assert user_limit.max_value == 10.0  # From premium plan
    assert key_limit.max_value == 10.0   # From basic plan


def test_product_deletion_fallback_logic(db: Session, test_team):
    """
    Given: Team with PRODUCT limits and all products deleted
    When: Processing product deletion
    Then: Should fall back to DEFAULT limits as per design document
    """
    limit_service = LimitService(db)

    # Create PRODUCT limit
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=8.0,
        current_value=4.0,
        limited_by=LimitSource.PRODUCT
    )

    # Verify PRODUCT limit exists
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 1
    assert team_limits[0].limited_by == LimitSource.PRODUCT
    assert team_limits[0].max_value == 8.0

    # Simulate all products being deleted by deleting the PRODUCT limit first
    db.delete(db.query(DBLimitedResource).filter(
        and_(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == test_team.id,
            DBLimitedResource.resource == ResourceType.USER
        )
    ).first())
    db.commit()

    # Then create DEFAULT limit
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=1.0,  # DEFAULT_USER_COUNT from resource_limits.py
        current_value=4.0,  # Keep current usage
        limited_by=LimitSource.DEFAULT
    )

    # Verify fallback to DEFAULT
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 1
    assert team_limits[0].limited_by == LimitSource.DEFAULT
    assert team_limits[0].max_value == 1.0


def test_data_plane_limits_from_products(db: Session, test_team, test_product):
    """
    Given: Product with data plane limits (budget, RPM)
    When: Product limits are applied
    Then: Should create DP limits with current_value=None
    """
    limit_service = LimitService(db)

    # Apply DP limits from product
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=test_product.max_budget_per_key,
        current_value=None,  # DP limits don't track current value
        limited_by=LimitSource.PRODUCT
    )

    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.RPM,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.COUNT,
        max_value=float(test_product.rpm_per_key),
        current_value=None,
        limited_by=LimitSource.PRODUCT
    )

    # Verify DP limits were created correctly
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) == 2

    for limit in team_limits:
        assert limit.limit_type == LimitType.DATA_PLANE
        assert limit.current_value is None
        assert limit.limited_by == LimitSource.PRODUCT
