import pytest
from datetime import datetime, UTC
from fastapi import HTTPException
from app.db.models import DBUser, DBPrivateAIKey, DBTeam
from app.core.limit_service import (
    LimitService,
    DEFAULT_KEYS_PER_USER,
    DEFAULT_SERVICE_KEYS,
)
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource


def test_create_key_within_limits(db, test_team, test_region):
    """Test creating an LLM token when within the default limits"""
    db.commit()

    # Test that check_key_limits doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, None)


def test_create_key_exceeding_total_limit(db, test_team, test_region):
    """Test creating a team key when it would exceed service key limit"""

    # Create service keys up to the limit
    for i in range(DEFAULT_SERVICE_KEYS):
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
            created_at=datetime.now(UTC),
        )
        db.add(key)
        db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    # Now that fallback creates a limit, subsequent calls use LimitService which returns generic message
    assert "Entity has reached their maximum number of AI keys" in str(
        exc_info.value.detail
    )


def test_create_key_exceeding_user_limit(db, test_team, test_region):
    """Test creating an LLM token when it would exceed user token limit"""
    db.commit()

    # Create a test user
    user = DBUser(
        email="testuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()

    # Create LLM tokens up to the user limit
    for i in range(DEFAULT_KEYS_PER_USER):
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
            created_at=datetime.now(UTC),
        )
        db.add(key)
        db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, user.id)
    assert exc_info.value.status_code == 402
    # Now that fallback creates a limit, subsequent calls use LimitService which returns generic message
    assert "Entity has reached their maximum number of AI keys" in str(
        exc_info.value.detail
    )


def test_create_key_exceeding_service_key_limit(db, test_team, test_region):
    """Test creating an LLM token when it would exceed service token limit"""
    db.commit()

    # Create service LLM tokens up to the limit
    for i in range(DEFAULT_SERVICE_KEYS):
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
            created_at=datetime.now(UTC),
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    assert (
        f"Team has reached the maximum service LLM key limit of {DEFAULT_SERVICE_KEYS} keys"
        in str(exc_info.value.detail)
    )


def test_create_key_with_multiple_users_default_limits(db, test_team, test_region):
    """Test creating user keys when team has multiple users"""

    # Create two users
    user1 = DBUser(
        email="user1@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    user2 = DBUser(
        email="user2@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
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
            created_at=datetime.now(UTC),
        )
        db.add(key)
    db.commit()

    # Test that check_key_limits raises an exception when trying to create another user key
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(
            test_team.id, user1.id
        )  # Try to create another key for user1
    assert exc_info.value.status_code == 402
    assert (
        f"User has reached the maximum LLM key limit of {DEFAULT_KEYS_PER_USER} keys"
        in str(exc_info.value.detail)
    )


def test_create_key_with_mixed_service_and_user_keys(db, test_team, test_region):
    """Test creating keys when team has a mix of service and user keys"""
    db.commit()

    # Create two users
    user1 = DBUser(
        email="user1@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    user2 = DBUser(
        email="user2@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=test_team.id,
        created_at=datetime.now(UTC),
    )
    db.add(user1)
    db.add(user2)
    db.commit()

    # Fill the team's service key allowance
    for i in range(DEFAULT_SERVICE_KEYS):
        db.add(
            DBPrivateAIKey(
                name=f"Test Service Key {i}",
                database_name=f"test_service_db_{i}",
                database_host="localhost",
                database_username="test_user",
                database_password="test_pass",
                litellm_token=f"test_service_token_{i}",
                owner_id=None,  # Service keys have no owner
                team_id=test_team.id,
                region_id=test_region.id,
                created_at=datetime.now(UTC),
            )
        )

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
        created_at=datetime.now(UTC),
    )
    db.add(user1_key)
    db.commit()

    # Test that check_key_limits raises an exception when trying to create another service key
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(
            test_team.id, None
        )  # Try to create another service key
    assert exc_info.value.status_code == 402
    assert (
        f"Team has reached the maximum service LLM key limit of {DEFAULT_SERVICE_KEYS} keys"
        in str(exc_info.value.detail)
    )


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
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        limited_by=LimitSource.DEFAULT,
    )

    # Test that check_key_limits doesn't raise an exception
    limit_service.check_key_limits(test_team.id, None)


def test_check_key_limits_with_limit_service_at_capacity(db, test_team):
    """
    GIVEN: A team with limits set up in the new limit service at capacity
    WHEN: Checking key limits
    THEN: The limit service is used first and raises an exception
    """
    # First create actual service keys to reach the limit
    # Create 5 service keys to actually reach the limit
    for i in range(5):
        key = DBPrivateAIKey(
            team_id=test_team.id,
            owner_id=None,  # Service keys have no owner
            litellm_token=f"service_key_{i}",
            created_at=datetime.now(UTC),
        )
        db.add(key)
    db.commit()

    # Set up a limit in the new service at capacity
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY,
        limit_type=LimitType.CONTROL_PLANE,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=5.0,  # At capacity
        limited_by=LimitSource.DEFAULT,
    )

    # Test that check_key_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_key_limits(test_team.id, None)
    assert exc_info.value.status_code == 402
    assert "Entity has reached their maximum number of AI keys" in str(
        exc_info.value.detail
    )


def test_check_key_limits_fallback_creates_limit(db, test_team):
    """
    GIVEN: A team with no limits in the new service
    WHEN: Checking key limits
    THEN: The fallback code runs and creates a new limit in the service
    """
    db.commit()

    # Verify no limit exists in the service initially
    limit_service = LimitService(db)
    try:
        limit_service.increment_resource(
            OwnerType.TEAM, test_team.id, ResourceType.SERVICE_KEY
        )
        assert False, "Should have raised LimitNotFoundError"
    except Exception:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, None)

    # Verify limit was created in the service by checking the team limits
    team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    team_limits = limit_service.get_team_limits(team)

    # Should have a SERVICE_KEY limit now
    key_limits = [
        limit for limit in team_limits if limit.resource == ResourceType.SERVICE_KEY
    ]
    assert len(key_limits) == 1
    key_limit = key_limits[0]
    assert key_limit.max_value == DEFAULT_SERVICE_KEYS
    assert key_limit.current_value == 1.0  # Should be 1 after the increment


def test_check_key_limits_fallback_creates_user_limit(db, test_team, test_team_user):
    """
    GIVEN: A team with no limits in the new service
    WHEN: Checking key limits for a specific user
    THEN: The fallback code runs and creates user-level limits in the service
    """
    db.commit()

    # Verify no limit exists in the service initially
    limit_service = LimitService(db)
    try:
        limit_service.increment_resource(
            OwnerType.USER, test_team_user.id, ResourceType.USER_KEY
        )
        assert False, "Should have raised LimitNotFoundError"
    except Exception:
        pass  # Expected

    # Call the function - should trigger fallback and create limit
    limit_service = LimitService(db)
    limit_service.check_key_limits(test_team.id, test_team_user.id)

    # Verify limit was created in the service by checking the user limits
    user_limits = limit_service.get_user_limits(test_team_user)

    # Should have a USER_KEY limit now
    key_limits = [
        limit for limit in user_limits if limit.resource == ResourceType.USER_KEY
    ]
    assert len(key_limits) == 1
    key_limit = key_limits[0]
    assert key_limit.max_value == DEFAULT_KEYS_PER_USER
    assert key_limit.current_value == 1.0  # Should be 1 after the increment
