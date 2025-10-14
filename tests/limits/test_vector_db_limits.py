import pytest
from datetime import datetime, UTC
from fastapi import HTTPException
from app.db.models import DBUser, DBProduct, DBTeamProduct, DBPrivateAIKey, DBTeam
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource


def test_create_vector_db_within_limits(db, test_team, test_product):
    """Test creating a vector DB when within product limits"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Test that check_vector_db_limits doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_vector_db_limits(test_team.id)


def test_create_vector_db_exceeding_limit(db, test_team, test_product, test_region):
    """Test creating a vector DB when it would exceed the limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create vector DBs up to the limit
    for i in range(test_product.vector_db_count):
        key = DBPrivateAIKey(
            name=f"Test Vector DB {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {test_product.vector_db_count} databases" in str(exc_info.value.detail)


def test_create_vector_db_with_default_limit(db, test_team, test_region):
    """Test creating vector DBs with default limit when team has no products"""
    from app.db.models import DBProduct, DBTeamProduct

    # Create a product with a specific vector DB limit for testing
    test_product = DBProduct(
        id="prod_test_vector_limit",
        name="Test Product Vector Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=2,  # Specific limit for testing
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create vector DBs up to the limit
    for i in range(test_product.vector_db_count):
        key = DBPrivateAIKey(
            name=f"Test Vector DB {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {test_product.vector_db_count} databases" in str(exc_info.value.detail)


def test_create_vector_db_with_multiple_products(db, test_team, test_region):
    """Test creating vector DBs when team has multiple products with different limits"""
    # Create two products with different vector DB limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
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
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
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

    # Create vector DBs up to the higher product limit (3) since fallback now works correctly
    for i in range(3):
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
        key = DBPrivateAIKey(
            name=f"Test Vector DB {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
        db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum vector DB limit" in str(exc_info.value.detail)


def test_create_vector_db_with_user_owned_key(db, test_team, test_region, test_team_user):
    """Test vector DB limit check when a user-owned key has a vector DB and team has no products"""
    from app.db.models import DBProduct, DBTeamProduct

    # Create a product with a specific vector DB limit for testing
    test_product = DBProduct(
        id="prod_test_user_vector_limit",
        name="Test Product User Vector Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=2,  # Specific limit for testing
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create user-owned keys with vector DBs up to the limit
    for i in range(test_product.vector_db_count):
        key = DBPrivateAIKey(
            name=f"Test User Vector DB {i}",
            database_name=f"test_user_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            owner_id=test_team_user.id,
            team_id=None,  # User-owned keys should not have team_id
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {test_product.vector_db_count} databases" in str(exc_info.value.detail)


def test_check_vector_db_limits_with_limit_service(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service
    WHEN: Checking vector DB limits
    THEN: The limit service is used first and succeeds
    """

    # Set up a limit in the new service
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.VECTOR_DB,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=3.0,
        current_value=1.0,
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_vector_db_limits doesn't raise an exception
    limit_service.check_vector_db_limits(test_team.id)


def test_check_vector_db_limits_with_limit_service_at_capacity(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service at capacity
    WHEN: Checking vector DB limits
    THEN: The limit service is used first and raises an exception
    """

    # Set up a limit in the new service at capacity
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.VECTOR_DB,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=2.0,
        current_value=2.0,  # At capacity
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum vector DB limit" in str(exc_info.value.detail)


def test_check_vector_db_limits_fallback_creates_limit(db, test_team, test_product):
    """
    GIVEN: A team with no limits in the new service but with products
    WHEN: Checking vector DB limits
    THEN: The fallback code runs and creates a new limit in the service
    """
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Verify no limit exists in the service initially
    limit_service = LimitService(db)
    try:
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.VECTOR_DB)
        assert False, "Should have raised LimitNotFoundError"
    except Exception:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_vector_db_limits(test_team.id)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a VECTOR_DB limit now
    vector_db_limits = [limit for limit in team_limits if limit.resource == ResourceType.VECTOR_DB]
    assert len(vector_db_limits) == 1
    vector_db_limit = vector_db_limits[0]
    # The fallback correctly uses product values (vector DB query was already correct)
    assert vector_db_limit.max_value == test_product.vector_db_count  # Should be 1
    assert vector_db_limit.current_value == 1.0  # Should be 1 after the increment
