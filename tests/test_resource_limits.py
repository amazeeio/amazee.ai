import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBUser, DBTeam, DBProduct, DBTeamProduct, DBPrivateAIKey
from datetime import datetime, UTC
from fastapi import HTTPException
from app.core.resource_limits import check_key_limits, check_team_user_limit

def test_add_user_within_product_limit(client, admin_token, db, test_team, test_product):
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

def test_add_user_exceeding_product_limit(client, admin_token, db, test_team, test_product):
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
    assert exc_info.value.status_code == 400
    assert f"Team has reached the maximum user limit of {test_product.user_count} users" in str(exc_info.value.detail)

def test_add_user_with_default_limit(client, admin_token, db, test_team):
    """Test adding users with default limit when team has no products"""
    # Create and add users up to the default limit (2)
    for i in range(2):
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
    assert exc_info.value.status_code == 400
    assert "Team has reached the maximum user limit of 2 users" in str(exc_info.value.detail)

def test_add_user_with_multiple_products(client, admin_token, db, test_team):
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
    assert exc_info.value.status_code == 400
    assert f"Team has reached the maximum user limit of {product2.user_count} users" in str(exc_info.value.detail)

def test_create_key_within_limits(client, admin_token, db, test_team, test_product, test_region):
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

def test_create_key_exceeding_total_limit(client, admin_token, db, test_team, test_product, test_region):
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
    assert exc_info.value.status_code == 400
    assert f"Team has reached the maximum LLM token limit of {test_product.total_key_count} tokens" in str(exc_info.value.detail)

def test_create_key_exceeding_user_limit(client, admin_token, db, test_team, test_product, test_region):
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
    assert exc_info.value.status_code == 400
    assert f"User has reached the maximum LLM token limit of {test_product.keys_per_user} tokens" in str(exc_info.value.detail)

def test_create_key_exceeding_service_key_limit(client, admin_token, db, test_team, test_product, test_region):
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
    assert exc_info.value.status_code == 400
    assert f"Team has reached the maximum service LLM token limit of {test_product.service_key_count} tokens" in str(exc_info.value.detail)

def test_create_key_with_default_limits(client, admin_token, db, test_team, test_region):
    """Test creating LLM tokens with default limits when team has no products"""
    # Create LLM tokens up to the default limit (2)
    for i in range(2):
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
    assert exc_info.value.status_code == 400
    assert "Team has reached the maximum LLM token limit of 2 tokens" in str(exc_info.value.detail)

def test_create_key_with_multiple_products(client, admin_token, db, test_team, test_region):
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
    assert exc_info.value.status_code == 400
    assert f"Team has reached the maximum LLM token limit of {product2.total_key_count} tokens" in str(exc_info.value.detail)

def test_create_key_with_multiple_users_default_limits(db, test_team, test_region):
    """Test creating a key when team has no products and multiple users have keys"""
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

    # Test that check_key_limits raises an exception when trying to create a team-owned key
    with pytest.raises(HTTPException) as exc_info:
        check_key_limits(db, test_team.id, None)
    assert exc_info.value.status_code == 400
    assert "Team has reached the maximum LLM token limit of 2 tokens" in str(exc_info.value.detail)
