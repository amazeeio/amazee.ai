import pytest
from datetime import datetime, UTC, timedelta
from app.db.models import DBUser, DBProduct, DBTeamProduct
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource
from app.core.limit_service import (
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
)


def test_get_token_restrictions_default_limits(db, test_team):
    """Test getting token restrictions when team has no products (using default limits)"""
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use default values since team has no products
    assert days_left == DEFAULT_KEY_DURATION  # 30 days
    assert max_spend == DEFAULT_MAX_SPEND  # 27.0
    assert rpm_limit == DEFAULT_RPM_PER_KEY  # 500


def test_get_token_restrictions_with_product(db, test_team, test_product):
    """Test getting token restrictions when team has a product"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use product values
    assert days_left == test_product.renewal_period_days  # 30 days
    assert max_spend == test_product.max_budget_per_key  # 50.0
    assert rpm_limit == test_product.rpm_per_key  # 1000


def test_get_token_restrictions_with_multiple_products(db, test_team):
    """Test getting token restrictions when team has multiple products with different limits"""
    # Create two products with different limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=3,
        keys_per_user=2,
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
    product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=75.0,  # Higher budget
        rpm_per_key=2000,  # Higher RPM
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=60,  # Longer duration
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add both products to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    team_product2 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product2.id
    )
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use the maximum values from both products
    assert days_left == product2.renewal_period_days  # 60 days
    assert max_spend == product2.max_budget_per_key  # 75.0
    assert rpm_limit == product2.rpm_per_key  # 2000


def test_get_token_restrictions_with_payment_history(db, test_team, test_product):
    """Test getting token restrictions when team has payment history"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Set created_at to 30 days ago and last_payment to 15 days ago
    now = datetime.now(UTC)
    test_team.created_at = now - timedelta(days=30)
    test_team.last_payment = now - timedelta(days=15)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should return the product's renewal_period_days, not calculated days left
    assert days_left == test_product.renewal_period_days  # 30 days
    assert max_spend == test_product.max_budget_per_key
    assert rpm_limit == test_product.rpm_per_key


def test_get_token_restrictions_team_not_found(db):
    """Test getting token restrictions for non-existent team"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.get_token_restrictions(99999)  # Non-existent team ID
    assert exc_info.value.status_code == 404
    assert "Team not found" in str(exc_info.value.detail)


def test_get_token_restrictions_with_limit_service(db, test_team):
    """
    GIVEN: A team with budget and RPM limits set up in the new limit service
    WHEN: Getting token restrictions
    THEN: The limit service is used first and returns the correct values
    """

    # Set up budget and RPM limits in the new service
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        limited_by=LimitSource.DEFAULT
    )
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.RPM,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.COUNT,
        max_value=1500.0,
        limited_by=LimitSource.DEFAULT
    )

    # Test that get_token_restrictions returns the limit service values
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)
    assert days_left == DEFAULT_KEY_DURATION  # Still uses product/default for duration
    assert max_spend == 100.0  # From limit service
    assert rpm_limit == 1500.0  # From limit service


def test_get_token_restrictions_with_limit_service_and_products(db, test_team, test_product):
    """
    GIVEN: A team with both limit service limits and product limits
    WHEN: Getting token restrictions
    THEN: The limit service values take precedence over product values
    """

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Set up budget and RPM limits in the new service (different from product)
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=200.0,  # Different from product's 50.0
        limited_by=LimitSource.DEFAULT
    )
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.RPM,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.COUNT,
        max_value=2500.0,  # Different from product's 1000
        limited_by=LimitSource.DEFAULT
    )

    # Test that get_token_restrictions returns the limit service values
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)
    assert days_left == test_product.renewal_period_days  # Still uses product for duration
    assert max_spend == 200.0  # From limit service, not product
    assert rpm_limit == 2500.0  # From limit service, not product


def test_get_product_max_by_type_no_products(db, test_team):
    """
    GIVEN: The team has no associated products
    WHEN: Trying to determine the correct limit value for a resource
    THEN: The default maximum value for the resource type is used
    """
    limit_service = LimitService(db)
    max_vectors = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.VECTOR_DB)
    assert max_vectors is None


def test_get_product_max_by_type_multiple_products(db, test_team):
    """
    GIVEN: The team has two associated products
    WHEN: Trying to determin the correct limit value for a resource
    THEN: The maximum value for the resource type is used
    """
    # Create two products with different vector DB limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=4,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=150.0,
        rpm_per_key=1000,
        vector_db_count=2,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=3,
        keys_per_user=2,
        total_key_count=15,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=800,
        vector_db_count=3,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add both products to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    team_product2 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product2.id
    )
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    limit_service = LimitService(db)
    max_vectors = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.VECTOR_DB)
    max_users = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.USER)
    max_keys = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.SERVICE_KEY)
    max_budget = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.BUDGET)
    assert max_vectors == 3
    assert max_users == 4
    assert max_keys == 2  # Now returns max service_key_count, not total_key_count
    assert max_budget == 150.0
