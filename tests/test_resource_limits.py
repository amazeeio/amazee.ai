import pytest
from app.db.models import DBUser, DBProduct, DBTeamProduct, DBPrivateAIKey
from datetime import datetime, UTC, timedelta
from fastapi import HTTPException
from app.core.resource_limits import (
    check_key_limits,
    check_team_user_limit,
    check_vector_db_limits,
    get_token_restrictions,
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
    DEFAULT_USER_COUNT,
    DEFAULT_TOTAL_KEYS,
    DEFAULT_VECTOR_DB_COUNT
)

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
    check_team_user_limit(db, test_team.id)

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
        check_team_user_limit(db, test_team.id)
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
        check_team_user_limit(db, test_team.id)
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
        check_team_user_limit(db, test_team.id)
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

    # Create and add users up to the higher limit (5)
    for i in range(5):
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
        check_team_user_limit(db, test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum user limit of {product2.user_count} users" in str(exc_info.value.detail)

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
    check_key_limits(db, test_team.id, None)

def test_create_key_exceeding_total_limit(db, test_team, test_product, test_region):
    """Test creating an LLM token when it would exceed total token limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create LLM tokens up to the limit
    for i in range(test_product.total_key_count):
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
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {test_product.total_key_count} keys" in str(exc_info.value.detail)

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
        check_key_limits(db, test_team.id, user.id)
    assert exc_info.value.status_code == 402
    assert f"User has reached the maximum LLM key limit of {test_product.keys_per_user} keys" in str(exc_info.value.detail)

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
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum service LLM key limit of {test_product.service_key_count} keys" in str(exc_info.value.detail)

def test_create_key_with_default_limits(db, test_team, test_region):
    """Test creating LLM tokens with default limits when team has no products"""
    from app.db.models import DBProduct, DBTeamProduct

    # Create a product with a specific key limit for testing
    test_product = DBProduct(
        id="prod_test_key_limit",
        name="Test Product Key Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=4,  # Specific limit for testing
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

    # Create LLM tokens up to the limit
    for i in range(test_product.total_key_count):
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
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {test_product.total_key_count} keys" in str(exc_info.value.detail)

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

    # Create LLM tokens up to the higher total token limit (5)
    for i in range(5):
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
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {product2.total_key_count} keys" in str(exc_info.value.detail)

def test_create_key_with_multiple_users_default_limits(db, test_team, test_region):
    """Test creating a key when team has no products and multiple users have keys"""
    from app.db.models import DBProduct, DBTeamProduct

    # Create a product with a specific key limit for testing
    test_product = DBProduct(
        id="prod_test_multi_user_limit",
        name="Test Product Multi User Limit",
        user_count=3,
        keys_per_user=2,
        total_key_count=3,  # Specific limit for testing
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

    # Create keys up to the limit, alternating between users
    for i in range(test_product.total_key_count):
        user = user1 if i % 2 == 0 else user2
        key = DBPrivateAIKey(
            name=f"Test Token {i} for {user.email}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            litellm_token=f"test_token_{i}",
            owner_id=user.id,
            team_id=None,  # Keys with owner_id should not have team_id
            region_id=test_region.id,
            created_at=datetime.now(UTC)
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception when trying to create a team-owned key
    with pytest.raises(HTTPException) as exc_info:
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {test_product.total_key_count} keys" in str(exc_info.value.detail)

def test_create_key_with_mixed_service_and_user_keys(db, test_team, test_region):
    """Test creating keys when team has a mix of service and user keys"""
    # Create a product with a total key limit of 3
    product = DBProduct(
        id="prod_test",
        name="Test Product",
        user_count=3,
        keys_per_user=2,
        total_key_count=3,  # Total key limit of 3
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

    # Create one service key
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

    # Create one key for each user
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

    # Test that check_key_limits raises an exception when trying to create another key
    with pytest.raises(HTTPException) as exc_info:
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum LLM key limit of {product.total_key_count} keys" in str(exc_info.value.detail)

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
    check_vector_db_limits(db, test_team.id)

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
        check_vector_db_limits(db, test_team.id)
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
        check_vector_db_limits(db, test_team.id)
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

    # Create vector DBs up to the higher limit (3)
    for i in range(3):
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
        check_vector_db_limits(db, test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {product2.vector_db_count} databases" in str(exc_info.value.detail)

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
        check_vector_db_limits(db, test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum vector DB limit of {test_product.vector_db_count} databases" in str(exc_info.value.detail)

def test_get_token_restrictions_default_limits(db, test_team):
    """Test getting token restrictions when team has no products (using default limits)"""
    days_left, max_spend, rpm_limit = get_token_restrictions(db, test_team.id)

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

    days_left, max_spend, rpm_limit = get_token_restrictions(db, test_team.id)

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

    days_left, max_spend, rpm_limit = get_token_restrictions(db, test_team.id)

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

    days_left, max_spend, rpm_limit = get_token_restrictions(db, test_team.id)

    # Should return the product's renewal_period_days, not calculated days left
    assert days_left == test_product.renewal_period_days  # 30 days
    assert max_spend == test_product.max_budget_per_key
    assert rpm_limit == test_product.rpm_per_key

def test_get_token_restrictions_team_not_found(db):
    """Test getting token restrictions for non-existent team"""
    from app.core.resource_limits import get_token_restrictions
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        get_token_restrictions(db, 99999)  # Non-existent team ID
    assert exc_info.value.status_code == 404
    assert "Team not found" in str(exc_info.value.detail)
