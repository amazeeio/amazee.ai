import pytest

from app.db.models import DBLimitedResource
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, OwnerType, LimitSource
from app.core.limit_service import (
    DEFAULT_KEYS_PER_USER,
    DEFAULT_SERVICE_KEYS,
)


def test_resource_type_enum_has_new_key_types():
    """
    Given: The ResourceType enum
    When: Checking for new key resource types
    Then: Should have USER_KEY and SERVICE_KEY defined
    """
    assert hasattr(ResourceType, 'USER_KEY')
    assert hasattr(ResourceType, 'SERVICE_KEY')
    assert ResourceType.USER_KEY.value == "user_key"
    assert ResourceType.SERVICE_KEY.value == "service_key"


def test_setup_default_limits_creates_separate_key_limits(db):
    """
    Given: A fresh database with no limits
    When: setup_default_limits is called
    Then: Should create separate system limits for USER_KEY and SERVICE_KEY
    """
    import os
    from app.core.limit_service import setup_default_limits

    # Enable limits for this test
    os.environ['ENABLE_LIMITS'] = 'true'

    try:
        # Setup default limits
        setup_default_limits(db)
    finally:
        # Clean up environment
        os.environ.pop('ENABLE_LIMITS', None)

    # Check that separate system limits were created for each key type
    user_key_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.SYSTEM,
        DBLimitedResource.owner_id == 0,
        DBLimitedResource.resource == ResourceType.USER_KEY
    ).first()
    assert user_key_limit is not None
    assert user_key_limit.max_value == DEFAULT_KEYS_PER_USER

    service_key_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.SYSTEM,
        DBLimitedResource.owner_id == 0,
        DBLimitedResource.resource == ResourceType.SERVICE_KEY
    ).first()
    assert service_key_limit is not None
    assert service_key_limit.max_value == DEFAULT_SERVICE_KEYS


def test_get_default_user_limit_uses_user_key_resource_type(db):
    """
    Given: A limit service with system defaults set up
    When: Getting default user limit for USER_KEY resource
    Then: Should return the correct default value
    """
    import os
    from app.core.limit_service import setup_default_limits

    # Enable limits and setup defaults
    os.environ['ENABLE_LIMITS'] = 'true'
    try:
        setup_default_limits(db)

        limit_service = LimitService(db)

        # Test USER_KEY resource type
        user_key_limit = limit_service.get_default_user_limit_for_resource(
            ResourceType.USER_KEY)
        assert user_key_limit == DEFAULT_KEYS_PER_USER
    finally:
        os.environ.pop('ENABLE_LIMITS', None)


def test_get_default_team_limit_uses_service_key_resource_type(db):
    """
    Given: A limit service with system defaults set up
    When: Getting default team limit for SERVICE_KEY resource
    Then: Should return the correct default value
    """
    import os
    from app.core.limit_service import setup_default_limits

    # Enable limits and setup defaults
    os.environ['ENABLE_LIMITS'] = 'true'
    try:
        setup_default_limits(db)

        limit_service = LimitService(db)

        # Test SERVICE_KEY resource type
        service_key_limit = limit_service.get_default_team_limit_for_resource(
            ResourceType.SERVICE_KEY)
        assert service_key_limit == DEFAULT_SERVICE_KEYS
    finally:
        os.environ.pop('ENABLE_LIMITS', None)


def test_set_user_limits_uses_user_key_resource_type(db, test_user):
    """
    Given: A user with no existing limits
    When: Calling set_user_limits(user)
    Then: Should create limits using USER_KEY resource type
    """
    limit_service = LimitService(db)

    # Set user limits
    limit_service.set_user_limits(test_user)

    # Check that USER_KEY limit was created
    user_limits = limit_service.get_user_limits(test_user)
    user_key_limit = next(
        (limit for limit in user_limits if limit.resource == ResourceType.USER_KEY), None)
    assert user_key_limit is not None
    assert user_key_limit.limited_by == LimitSource.DEFAULT
    assert user_key_limit.max_value == DEFAULT_KEYS_PER_USER


def test_set_team_limits_uses_service_key_resource_type(db, test_team):
    """
    Given: A team with no existing limits
    When: Calling set_team_limits(team)
    Then: Should create limits using SERVICE_KEY resource type
    """
    limit_service = LimitService(db)

    # Set team limits
    limit_service.set_team_limits(test_team)

    # Check that SERVICE_KEY limit was created
    team_limits = limit_service.get_team_limits(test_team)
    service_key_limit = next(
        (limit for limit in team_limits if limit.resource == ResourceType.SERVICE_KEY), None)
    assert service_key_limit is not None
    assert service_key_limit.limited_by == LimitSource.DEFAULT
    assert service_key_limit.max_value == DEFAULT_SERVICE_KEYS


def test_increment_user_key_resource(db, test_user):
    """
    Given: A user with USER_KEY limit set
    When: Incrementing USER_KEY resource
    Then: Should successfully increment the current value
    """
    limit_service = LimitService(db)

    # Set user limits first
    limit_service.set_user_limits(test_user)

    # Increment user key resource
    limit_service.increment_resource(
        owner_type=OwnerType.USER,
        owner_id=test_user.id,
        resource_type=ResourceType.USER_KEY
    )

    # Check that current value was incremented
    user_limits = limit_service.get_user_limits(test_user)
    user_key_limit = next(
        (limit for limit in user_limits if limit.resource == ResourceType.USER_KEY), None)
    assert user_key_limit is not None
    assert user_key_limit.current_value == 1.0


def test_increment_service_key_resource(db, test_team):
    """
    Given: A team with SERVICE_KEY limit set
    When: Incrementing SERVICE_KEY resource
    Then: Should successfully increment the current value
    """
    limit_service = LimitService(db)

    # Set team limits first
    limit_service.set_team_limits(test_team)

    # Increment service key resource
    limit_service.increment_resource(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.SERVICE_KEY
    )

    # Check that current value was incremented
    team_limits = limit_service.get_team_limits(test_team)
    service_key_limit = next(
        (limit for limit in team_limits if limit.resource == ResourceType.SERVICE_KEY), None)
    assert service_key_limit is not None
    assert service_key_limit.current_value == 1.0
