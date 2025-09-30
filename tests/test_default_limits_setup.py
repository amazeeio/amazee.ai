import pytest
import os
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.db.models import DBLimitedResource
from app.core.limit_service import LimitService, setup_default_limits
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


@patch.dict(os.environ, {'ENABLE_LIMITS': 'true'})
def test_setup_default_limits_creates_all_defaults(db: Session):
    """
    Given: ENABLE_LIMITS is true and no default limits exist
    When: Running setup_default_limits
    Then: Should create all default limits with current constant values
    """
    limit_service = LimitService(db)

    # Ensure no default limits exist initially
    existing_limits = limit_service.get_system_limits()
    assert len(existing_limits.limits) == 0

    # Run setup
    setup_default_limits(db)

    # Verify all default limits were created
    system_limits = limit_service.get_system_limits()
    assert len(system_limits.limits) > 0

    # Check specific limits exist
    resource_types = [limit.resource for limit in system_limits.limits]
    assert ResourceType.USER in resource_types
    assert ResourceType.KEY in resource_types
    assert ResourceType.VECTOR_DB in resource_types
    assert ResourceType.BUDGET in resource_types
    assert ResourceType.RPM in resource_types

    # Verify all limits are SYSTEM owned with DEFAULT source
    for limit in system_limits.limits:
        assert limit.owner_type == OwnerType.SYSTEM
        assert limit.owner_id == 0
        assert limit.limited_by == LimitSource.DEFAULT


@patch.dict(os.environ, {'ENABLE_LIMITS': 'true'})
def test_setup_default_limits_uses_current_constant_values(db: Session):
    """
    Given: ENABLE_LIMITS is true
    When: Running setup_default_limits
    Then: Should use the current constant values from limit_service
    """
    limit_service = LimitService(db)

    # Run setup
    setup_default_limits(db)

    # Get the created limits
    system_limits = limit_service.get_system_limits()

    # Find specific limits and verify values match constants
    user_limit = next((l for l in system_limits.limits if l.resource == ResourceType.USER), None)
    assert user_limit is not None
    assert user_limit.max_value == 1.0  # DEFAULT_USER_COUNT

    key_limit = next((l for l in system_limits.limits if l.resource == ResourceType.KEY), None)
    assert key_limit is not None
    assert key_limit.max_value == 6.0  # DEFAULT_TOTAL_KEYS

    vector_db_limit = next((l for l in system_limits.limits if l.resource == ResourceType.VECTOR_DB), None)
    assert vector_db_limit is not None
    assert vector_db_limit.max_value == 5.0  # DEFAULT_VECTOR_DB_COUNT

    budget_limit = next((l for l in system_limits.limits if l.resource == ResourceType.BUDGET), None)
    assert budget_limit is not None
    assert budget_limit.max_value == 27.0  # DEFAULT_MAX_SPEND

    rpm_limit = next((l for l in system_limits.limits if l.resource == ResourceType.RPM), None)
    assert rpm_limit is not None
    assert rpm_limit.max_value == 500.0  # DEFAULT_RPM_PER_KEY


@patch.dict(os.environ, {'ENABLE_LIMITS': 'true'})
def test_setup_default_limits_idempotent(db: Session):
    """
    Given: Default limits already exist
    When: Running setup_default_limits again
    Then: Should not create duplicates or modify existing limits
    """
    limit_service = LimitService(db)

    # Run setup first time
    setup_default_limits(db)
    first_run_limits = limit_service.get_system_limits()
    first_run_count = len(first_run_limits.limits)

    # Run setup second time
    setup_default_limits(db)
    second_run_limits = limit_service.get_system_limits()
    second_run_count = len(second_run_limits.limits)

    # Should have same number of limits
    assert first_run_count == second_run_count

    # Should have same limit values
    for first_limit in first_run_limits.limits:
        second_limit = next((l for l in second_run_limits.limits if l.resource == first_limit.resource), None)
        assert second_limit is not None
        assert second_limit.max_value == first_limit.max_value
        assert second_limit.limit_type == first_limit.limit_type
        assert second_limit.unit == first_limit.unit


@patch.dict(os.environ, {'ENABLE_LIMITS': 'false'})
def test_setup_default_limits_skipped_when_disabled(db: Session):
    """
    Given: ENABLE_LIMITS is false
    When: Running setup_default_limits
    Then: Should not create any default limits
    """
    limit_service = LimitService(db)

    # Run setup
    setup_default_limits(db)

    # Verify no limits were created
    system_limits = limit_service.get_system_limits()
    assert len(system_limits.limits) == 0


@patch.dict(os.environ, {'ENABLE_LIMITS': 'true'})
def test_setup_default_limits_runs_when_enabled(db: Session):
    """
    Given: ENABLE_LIMITS is true
    When: Running setup_default_limits
    Then: Should create default limits
    """
    limit_service = LimitService(db)

    # Run setup
    setup_default_limits(db)

    # Verify limits were created
    system_limits = limit_service.get_system_limits()
    assert len(system_limits.limits) > 0


@patch.dict(os.environ, {'ENABLE_LIMITS': 'true'})
def test_setup_default_limits_control_plane_limits_have_current_value(db: Session):
    """
    Given: ENABLE_LIMITS is true
    When: Running setup_default_limits
    Then: Control plane limits should have current_value set to 0.0
    """
    limit_service = LimitService(db)

    # Run setup
    setup_default_limits(db)

    # Get the created limits
    system_limits = limit_service.get_system_limits()

    # Check control plane limits have current_value
    for limit in system_limits.limits:
        if limit.limit_type == LimitType.CONTROL_PLANE:
            assert limit.current_value == 0.0
        elif limit.limit_type == LimitType.DATA_PLANE:
            assert limit.current_value is None
