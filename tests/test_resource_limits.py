import pytest
from app.db.models import DBUser, DBProduct, DBTeamProduct, DBPrivateAIKey, DBTeam
from datetime import datetime, UTC, timedelta
from fastapi import HTTPException
from app.core.limit_service import (
    LimitService,
    LimitNotFoundError,
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
    DEFAULT_USER_COUNT,
    DEFAULT_TOTAL_KEYS,
    DEFAULT_VECTOR_DB_COUNT
)
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource

def test_add_user_within_product_limit(db, test_team, test_product):
    """Test adding a user when within product user limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Test that check_team_user_limit doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_team_user_limit(test_team.id)

def test_add_user_exceeding_product_limit(db, test_team, test_product):
    """Test adding a user when it would exceed product user limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create and add users up to the limit
    for i in range(test_product.user_count):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum user limit of {test_product.user_count} users" in str(exc_info.value.detail)

def test_add_user_with_default_limit(db, test_team):
    """Test adding users with default limit when team has no products"""
    from app.db.models import DBProduct, DBTeamProduct

    # Create a product with a specific user limit for testing
    test_product = DBProduct(
        id="prod_test_user_limit",
        name="Test Product User Limit",
        user_count=3,  # Specific limit for testing
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
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create and add users up to the limit
    for i in range(test_product.user_count):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum user limit of {test_product.user_count} users" in str(exc_info.value.detail)

def test_add_user_with_one_product(db, test_team):
    """Test adding users when team has multiple products with different limits"""
    # Create two products with different user limits
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
        user_count=5,
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
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add product to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    db.add(team_product1)
    db.commit()

    # Create and add users up to the higher limit (5)
    for i in range(3):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum user limit of {product1.user_count} users" in str(exc_info.value.detail)

def test_add_user_with_multiple_products(db, test_team):
    """Test adding users when team has multiple products with different limits"""
    # Create two products with different user limits
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
        user_count=5,
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

    # Create and add users up to the higher product limit (5) since fallback now works correctly
    for i in range(5):
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
        db.commit()

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum user limit" in str(exc_info.value.detail)

def test_create_key_within_limits(db, test_team, test_product, test_region):
    """Test creating an LLM token when within product limits"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Test that check_key_limits doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, None)

# Key limit shape issue
def test_create_key_exceeding_total_limit(db, test_team, test_product, test_region):
    """Test creating a team key when it would exceed service key limit"""
    # Add product to team
    test_product.service_key_count = 2  # Set a low service key limit
    db.add(test_product)
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create service keys up to the limit
    for i in range(test_product.service_key_count):
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
        key = DBPrivateAIKey(
            name=f"Test Service Key {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",
            owner_id=None,  # Service key
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
        db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    # Now that fallback creates a limit, subsequent calls use LimitService which returns generic message
    assert "Entity has reached their maximum number of AI keys" in str(exc_info.value.detail)

def test_create_key_exceeding_user_limit(db, test_team, test_product, test_region):
    """Test creating an LLM token when it would exceed user token limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create a test user
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

    # Create LLM tokens up to the user limit
    for i in range(test_product.keys_per_user):
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, user.id)
        key = DBPrivateAIKey(
            name=f"Test Token {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",  # Add LLM token
            owner_id=user.id,
            team_id=None,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
        db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, user.id)
    assert exc_info.value.status_code == 402
    # Now that fallback creates a limit, subsequent calls use LimitService which returns generic message
    assert "Entity has reached their maximum number of AI keys" in str(exc_info.value.detail)

def test_create_key_exceeding_service_key_limit(db, test_team, test_product, test_region):
    """Test creating an LLM token when it would exceed service token limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create service LLM tokens up to the limit
    for i in range(test_product.service_key_count):
        key = DBPrivateAIKey(
            name=f"Test Service Token {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",  # Add LLM token
            owner_id=None,  # Service tokens have no owner
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum service LLM key limit of {test_product.service_key_count} keys" in str(exc_info.value.detail)

def test_create_key_with_default_limits(db, test_team, test_region):
    """Test creating team keys with default limits when team has no products"""

    # Create a product with a specific service key limit for testing
    test_product = DBProduct(
        id="prod_test_key_limit",
        name="Test Product Key Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=4,  # This is no longer used
        service_key_count=2,  # This is what matters for team keys
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
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

    # Create service keys up to the limit
    for i in range(test_product.service_key_count):
        key = DBPrivateAIKey(
            name=f"Test Service Key {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",
            owner_id=None,  # Service key
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum service LLM key limit of {test_product.service_key_count} keys" in str(exc_info.value.detail)

def test_create_key_with_multiple_products(db, test_team, test_region):
    """Test creating LLM tokens when team has multiple products with different limits"""
    # Create two products with different token limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=3,
        keys_per_user=2,
        total_key_count=3,
        service_key_count=1,
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
        keys_per_user=3,
        total_key_count=5,
        service_key_count=5,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
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

    # Create LLM tokens up to the higher total token limit (5)
    for i in range(5):
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
        key = DBPrivateAIKey(
            name=f"Test Token {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",  # Add LLM token
            owner_id=None,
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
        db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    # Now that fallback creates a limit, subsequent calls use LimitService which returns generic message
    assert "Entity has reached their maximum number of AI keys" in str(exc_info.value.detail)

def test_create_key_with_multiple_users_default_limits(db, test_team, test_region):
    """Test creating user keys when team has multiple users"""

    # Create a product with a specific key limit for testing
    test_product = DBProduct(
        id="prod_test_multi_user_limit",
        name="Test Product Multi User Limit",
        user_count=3,
        keys_per_user=1,  # Each user can have 1 key
        total_key_count=3,  # This is no longer used
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
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

    # Create two users
    user1 = DBUser(
        email="user1@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    user2 = DBUser(
        email="user2@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user1)
    db.add(user2)
    db.commit()

    # Create keys for each user up to their limit
    for user in [user1, user2]:
        key = DBPrivateAIKey(
            name=f"Test Token for {user.email}",
            database_name=f"test_db_{user.id}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{user.id}",
            owner_id=user.id,
            team_id=None,  # Keys with owner_id should not have team_id
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception when trying to create another user key
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, user1.id)  # Try to create another key for user1
    assert exc_info.value.status_code == 402
    assert f"User has reached the maximum LLM key limit of {test_product.keys_per_user} keys" in str(exc_info.value.detail)

def test_create_key_with_mixed_service_and_user_keys(db, test_team, test_region):
    """Test creating keys when team has a mix of service and user keys"""
    # Create a product with service key limit of 1 and user key limit of 1
    product = DBProduct(
        id="prod_test",
        name="Test Product",
        user_count=3,
        keys_per_user=1,  # Each user can have 1 key
        total_key_count=3,  # This is no longer used
        service_key_count=1,  # Team can have 1 service key
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product)
    db.commit()

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    # Create two users
    user1 = DBUser(
        email="user1@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    user2 = DBUser(
        email="user2@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC)
    )
    db.add(user1)
    db.add(user2)
    db.commit()

    # Create one service key (hits the service key limit)
    service_key = DBPrivateAIKey(
        name="Test Service Key",
        database_name="test_service_db",
        database_host="localhost",
        database_username="test_user",
        database_password="test_pass",
        litellm_token="test_service_token",
        owner_id=None,  # Service key has no owner
        team_id=test_team.id,
        region_id=test_region.id,
        created_at=datetime.now(UTC)
    )
    db.add(service_key)

    # Create one key for user1 (hits the user key limit for user1)
    user1_key = DBPrivateAIKey(
        name=f"Test Token for {user1.email}",
        database_name=f"test_db_{user1.id}",
        database_host="localhost",
        database_username="test_user",
        database_password="test_pass",
        litellm_token=f"test_token_{user1.id}",
        owner_id=user1.id,
        team_id=None,  # Keys with owner_id should not have team_id
        region_id=test_region.id,
        created_at=datetime.now(UTC)
    )
    db.add(user1_key)
    db.commit()

    # Test that check_key_limits raises an exception when trying to create another service key
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)  # Try to create another service key
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum service LLM key limit of {product.service_key_count} keys" in str(exc_info.value.detail)

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
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_team_user_limit doesn't raise an exception
    limit_service.check_team_user_limit(test_team.id)

def test_check_team_user_limit_with_limit_service_at_capacity(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service at capacity
    WHEN: Checking team user limits
    THEN: The limit service is used first and raises an exception
    """
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
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_team_user_limit raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum user limit" in str(exc_info.value.detail)

def test_check_team_user_limit_fallback_creates_limit(db, test_team, test_product):
    """
    GIVEN: A team with no limits in the new service but with products
    WHEN: Checking team user limits
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
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.USER)
        assert False, "Should have raised LimitNotFoundError"
    except LimitNotFoundError:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_team_user_limit(test_team.id)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a USER limit now
    user_limits = [limit for limit in team_limits.limits if limit.resource == ResourceType.USER]
    assert len(user_limits) == 1
    user_limit = user_limits[0]
    # The fallback now correctly uses product values after fixing the query
    assert user_limit.max_value == test_product.user_count  # Should be 5
    assert user_limit.current_value == 1.0  # Should be 1 after the increment

def test_check_key_limits_with_limit_service(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service
    WHEN: Checking key limits
    THEN: The limit service is used first and succeeds
    """
    # Set up a limit in the new service
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_key_limits doesn't raise an exception
    limit_service.check_key_limits(test_team.id, None)

def test_check_key_limits_with_limit_service_at_capacity(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service at capacity
    WHEN: Checking key limits
    THEN: The limit service is used first and raises an exception
    """
    # Set up a limit in the new service at capacity
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=5.0,  # At capacity
        limited_by=LimitSource.DEFAULT
    )

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    assert "Entity has reached their maximum number of AI keys" in str(exc_info.value.detail)

def test_check_key_limits_fallback_creates_limit(db, test_team, test_product):
    """
    GIVEN: A team with no limits in the new service but with products
    WHEN: Checking key limits
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
        limit_service.increment_resource(OwnerType.TEAM, test_team.id, ResourceType.KEY)
        assert False, "Should have raised LimitNotFoundError"
    except LimitNotFoundError:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, None)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a KEY limit now
    key_limits = [limit for limit in team_limits.limits if limit.resource == ResourceType.KEY]
    assert len(key_limits) == 1
    key_limit = key_limits[0]
    # The fallback should correctly use service_key_count (not total_key_count)
    assert key_limit.max_value == test_product.service_key_count  # Should be 2, not 10
    assert key_limit.current_value == 1.0  # Should be 1 after the increment

def test_check_key_limits_fallback_creates_user_limit(db, test_team, test_product, test_team_user):
    """
    GIVEN: A team with no limits in the new service but with products
    WHEN: Checking key limits for a specific user
    THEN: The fallback code runs and creates user-level limits in the service
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
        limit_service.increment_resource(OwnerType.USER, test_team_user.id, ResourceType.KEY)
        assert False, "Should have raised LimitNotFoundError"
    except LimitNotFoundError:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, test_team_user.id)

    # Verify limit was created in the service by checking the user limits
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should have a KEY limit now
    key_limits = [limit for limit in user_limits.limits if limit.resource == ResourceType.KEY]
    assert len(key_limits) == 1
    key_limit = key_limits[0]
    # The fallback should correctly use product values
    assert key_limit.max_value == test_product.keys_per_user  # Should be 2
    assert key_limit.current_value == 1.0  # Should be 1 after the increment

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
    except LimitNotFoundError:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_vector_db_limits(test_team.id)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a VECTOR_DB limit now
    vector_db_limits = [limit for limit in team_limits.limits if limit.resource == ResourceType.VECTOR_DB]
    assert len(vector_db_limits) == 1
    vector_db_limit = vector_db_limits[0]
    # The fallback correctly uses product values (vector DB query was already correct)
    assert vector_db_limit.max_value == test_product.vector_db_count  # Should be 1
    assert vector_db_limit.current_value == 1.0  # Should be 1 after the increment

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
    max_keys = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.KEY)
    max_budget = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.BUDGET)
    assert max_vectors == 3
    assert max_users == 4
    assert max_keys == 2  # Now returns max service_key_count, not total_key_count
    assert max_budget == 150.0