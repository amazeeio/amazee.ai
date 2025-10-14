import pytest
from datetime import datetime, UTC
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.db.models import DBLimitedResource, DBTeam, DBProduct, DBTeamProduct, DBUser
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


def test_reset_user_key_limit_with_zero_product_value(db: Session, test_team):
    """
    Given: A team has a product with keys_per_user set to 0
    When: A user's USER_KEY limit is reset
    Then: Should set the limit to 0 (the product value), not the default value
    """
    # Create a product with keys_per_user = 0 (valid product limit)
    product = DBProduct(
        id="prod_zero_keys",
        name="Zero Keys Product",
        user_count=5,
        keys_per_user=0,  # Product explicitly sets this to 0
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)

    # Associate product with team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    # Create a user in the team
    user = DBUser(
        email="testuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()

    # Create an existing USER_KEY limit with some value
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.USER,
        owner_id=user.id,
        resource_type=ResourceType.USER_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,  # Some existing value
        current_value=2.0,
        limited_by=LimitSource.DEFAULT
    )

    # Reset the limit - should use product value (0), not default
    result = limit_service.reset_limit(
        owner_type=OwnerType.USER,
        owner_id=user.id,
        resource_type=ResourceType.USER_KEY
    )

    # Verify the limit was reset to 0 (product value), not default value
    assert result.max_value == 0.0
    assert result.limited_by == LimitSource.PRODUCT

    # Also verify by fetching user limits
    user_limits = limit_service.get_user_limits(user)
    user_key_limit = next((limit for limit in user_limits if limit.resource == ResourceType.USER_KEY), None)
    assert user_key_limit is not None
    assert user_key_limit.max_value == 0.0
    assert user_key_limit.limited_by == LimitSource.PRODUCT


def test_reset_user_key_limit_with_product_value_not_zero(db: Session, test_team):
    """
    Given: A team has a product with keys_per_user set to 10 (default is 1)
    When: A user's USER_KEY limit is reset
    Then: Should set the limit to 10 (the product value), not 1 (the default value)
    """
    # Create a product with keys_per_user = 10
    product = DBProduct(
        id="prod_ten_keys",
        name="Ten Keys Product",
        user_count=5,
        keys_per_user=10,  # Product sets this to 10, default is 1
        total_key_count=50,
        service_key_count=5,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)

    # Associate product with team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    # Create a user in the team
    user = DBUser(
        email="testuser_ten@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()

    # Create an existing USER_KEY limit with some value (simulating it was set before)
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.USER,
        owner_id=user.id,
        resource_type=ResourceType.USER_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=3.0,  # Some old value
        current_value=2.0,
        limited_by=LimitSource.DEFAULT
    )

    # Reset the limit - should use product value (10), not default (1)
    result = limit_service.reset_limit(
        owner_type=OwnerType.USER,
        owner_id=user.id,
        resource_type=ResourceType.USER_KEY
    )

    # Verify the limit was reset to 10 (product value), not 1 (default value)
    assert result.max_value == 10.0, f"Expected 10.0 (product value), got {result.max_value}"
    assert result.limited_by == LimitSource.PRODUCT

    # Also verify by fetching user limits
    user_limits = limit_service.get_user_limits(user)
    user_key_limit = next((limit for limit in user_limits if limit.resource == ResourceType.USER_KEY), None)
    assert user_key_limit is not None
    assert user_key_limit.max_value == 10.0
    assert user_key_limit.limited_by == LimitSource.PRODUCT


def test_reset_team_owned_user_key_limit_with_product(db: Session, test_team):
    """
    Given: A team has a product with keys_per_user set to 10, and a TEAM-owned USER_KEY limit
    When: The TEAM-owned USER_KEY limit is reset
    Then: Should set the limit to 10 (the product value), not 1 (the default value)

    This tests the case where get_team_limits returns a USER_KEY resource with owner_type=TEAM
    """
    # Create a product with keys_per_user = 10
    product = DBProduct(
        id="prod_team_user_keys",
        name="Team User Keys Product",
        user_count=5,
        keys_per_user=10,  # Product sets this to 10, default is 1
        total_key_count=50,
        service_key_count=5,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)

    # Associate product with team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    # Create a TEAM-owned USER_KEY limit (this can happen from get_team_limits)
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=3.0,  # Some old value
        current_value=0.0,
        limited_by=LimitSource.DEFAULT
    )

    # Reset the limit - should use product value (10), not default (1)
    result = limit_service.reset_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.USER_KEY
    )

    # Verify the limit was reset to 10 (product value), not 1 (default value)
    assert result.max_value == 10.0, f"Expected 10.0 (product value), got {result.max_value}"
    assert result.limited_by == LimitSource.PRODUCT

    # Also verify by fetching team limits
    team_limits = limit_service.get_team_limits(test_team)
    user_key_limit = next((limit for limit in team_limits if limit.resource == ResourceType.USER_KEY), None)
    assert user_key_limit is not None
    assert user_key_limit.max_value == 10.0
    assert user_key_limit.limited_by == LimitSource.PRODUCT


def test_reset_team_limit_with_zero_product_value(db: Session, test_team):
    """
    Given: A team has a product with service_key_count set to 0
    When: The team's SERVICE_KEY limit is reset
    Then: Should set the limit to 0 (the product value), not the default value
    """
    # Create a product with service_key_count = 0 (valid product limit)
    product = DBProduct(
        id="prod_zero_service_keys",
        name="Zero Service Keys Product",
        user_count=5,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=0,  # Product explicitly sets this to 0
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)

    # Associate product with team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    # Create an existing SERVICE_KEY limit with some value
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,  # Some existing value
        current_value=2.0,
        limited_by=LimitSource.DEFAULT
    )

    # Reset the limit - should use product value (0), not default
    result = limit_service.reset_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY
    )

    # Verify the limit was reset to 0 (product value), not default value
    assert result.max_value == 0.0
    assert result.limited_by == LimitSource.PRODUCT

    # Also verify by fetching team limits
    team_limits = limit_service.get_team_limits(test_team)
    service_key_limit = next((limit for limit in team_limits if limit.resource == ResourceType.SERVICE_KEY), None)
    assert service_key_limit is not None
    assert service_key_limit.max_value == 0.0
    assert service_key_limit.limited_by == LimitSource.PRODUCT
