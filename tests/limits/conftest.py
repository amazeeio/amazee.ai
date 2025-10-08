import pytest
from datetime import datetime, UTC
from app.db.models import DBLimitedResource
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


@pytest.fixture
def limit_test_scenarios():
    """Common limit test scenarios"""
    return {
        "within_limits": {"current": 2, "max": 5, "should_pass": True},
        "at_capacity": {"current": 5, "max": 5, "should_pass": False},
        "exceeding_limits": {"current": 6, "max": 5, "should_pass": False},
    }


@pytest.fixture
def sample_team_limit(db, test_team):
    """Sample team limit for testing"""
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()
    return limit


@pytest.fixture
def sample_user_limit(db, test_team_user):
    """Sample user limit for testing"""
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=3.0,
        current_value=1.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()
    return limit


@pytest.fixture
def sample_system_limit(db):
    """Sample system limit for testing"""
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=1.0,
        current_value=0.0,
        owner_type=OwnerType.SYSTEM,
        owner_id=0,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()
    return limit
