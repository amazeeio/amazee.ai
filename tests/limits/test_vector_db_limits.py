import pytest
from datetime import datetime, UTC
from fastapi import HTTPException
from app.db.models import DBPrivateAIKey, DBTeam
from app.core.limit_service import LimitService, DEFAULT_VECTOR_DB_COUNT
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource


def test_create_vector_db_within_limits(db, test_team):
    """Test creating a vector DB when within the default limit"""
    db.commit()

    # Test that check_vector_db_limits doesn't raise an exception
    limit_service = LimitService(db)
    limit_service.check_vector_db_limits(test_team.id)


def test_create_vector_db_exceeding_limit(db, test_team, test_region):
    """Test creating a vector DB when it would exceed the limit"""
    db.commit()

    # Create vector DBs up to the limit
    for i in range(DEFAULT_VECTOR_DB_COUNT):
        key = DBPrivateAIKey(
            name=f"Test Vector DB {i}",
            database_name=f"test_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC),
        )
        db.add(key)
    db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert (
        f"Team has reached the maximum vector DB limit of {DEFAULT_VECTOR_DB_COUNT} databases"
        in str(exc_info.value.detail)
    )


def test_create_vector_db_with_user_owned_key(
    db, test_team, test_region, test_team_user
):
    """User-owned vector DBs count against the team limit"""

    # Create user-owned keys with vector DBs up to the limit
    for i in range(DEFAULT_VECTOR_DB_COUNT):
        key = DBPrivateAIKey(
            name=f"Test User Vector DB {i}",
            database_name=f"test_user_db_{i}",
            database_host="localhost",
            database_username="test_user",
            database_password="test_pass",
            owner_id=test_team_user.id,
            team_id=None,  # User-owned keys should not have team_id
            region_id=test_region.id,
            created_at=datetime.now(UTC),
        )
        db.add(key)
    db.commit()

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert (
        f"Team has reached the maximum vector DB limit of {DEFAULT_VECTOR_DB_COUNT} databases"
        in str(exc_info.value.detail)
    )


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
        limited_by=LimitSource.DEFAULT,
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
        limited_by=LimitSource.DEFAULT,
    )

    # Test that check_vector_db_limits raises an exception
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_vector_db_limits(test_team.id)
    assert exc_info.value.status_code == 402
    assert "Team has reached their maximum vector DB limit" in str(
        exc_info.value.detail
    )


def test_check_vector_db_limits_fallback_creates_limit(db, test_team):
    """
    GIVEN: A team with no limits in the new service
    WHEN: Checking vector DB limits
    THEN: The fallback code runs and creates a new limit in the service
    """
    db.commit()

    # Verify no limit exists in the service initially
    limit_service = LimitService(db)
    try:
        limit_service.increment_resource(
            OwnerType.TEAM, test_team.id, ResourceType.VECTOR_DB
        )
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
    vector_db_limits = [
        limit for limit in team_limits if limit.resource == ResourceType.VECTOR_DB
    ]
    assert len(vector_db_limits) == 1
    vector_db_limit = vector_db_limits[0]
    assert vector_db_limit.max_value == DEFAULT_VECTOR_DB_COUNT
    assert vector_db_limit.current_value == 1.0  # Should be 1 after the increment
