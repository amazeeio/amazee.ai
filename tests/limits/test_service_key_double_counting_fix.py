import pytest
from datetime import datetime, UTC
from app.db.models import DBPrivateAIKey, DBUser, DBTeam, DBProduct, DBTeamProduct, DBLimitedResource
from app.core.limit_service import LimitService
from app.core.worker import set_team_and_user_limits
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


@pytest.fixture
def test_team_with_keys_and_users(db, test_team):
    """
    Fixture that sets up a team with 77 service keys and 2 user keys.
    This represents the production scenario where double counting was occurring.
    """
    # Create 2 users for the team
    user1 = DBUser(
        email="user1@example.com",
        team_id=test_team.id,
        role="user",
        created_at=datetime.now(UTC)
    )
    user2 = DBUser(
        email="user2@example.com",
        team_id=test_team.id,
        role="user",
        created_at=datetime.now(UTC)
    )
    db.add(user1)
    db.add(user2)
    db.commit()

    # Create 77 service keys (owner_id=None, team_id=team_id)
    for i in range(77):
        key = DBPrivateAIKey(
            team_id=test_team.id,
            owner_id=None,  # Service keys have no owner
            litellm_token=f"service_key_{i}",
            created_at=datetime.now(UTC)
        )
        db.add(key)

    # Create 2 user keys (owner_id=user_id, team_id=team_id)
    for i, user in enumerate([user1, user2]):
        key = DBPrivateAIKey(
            team_id=test_team.id,
            owner_id=user.id,  # User keys have an owner
            litellm_token=f"user_key_{i}",
            created_at=datetime.now(UTC)
        )
        db.add(key)

    db.commit()

    # Create a product with service key limit of 150
    product = DBProduct(
        id="test_product",
        name="Test Product",
        service_key_count=150,
        created_at=datetime.now(UTC)
    )
    db.add(product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)
    db.commit()

    return {
        'team': test_team,
        'user1': user1,
        'user2': user2,
        'product': product
    }


def get_actual_counts(db, team_id):
    """Helper function to get the actual counts in the database."""
    total_keys = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.team_id == team_id,
        DBPrivateAIKey.litellm_token.isnot(None)
    ).count()

    service_key_count = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.team_id == team_id,
        DBPrivateAIKey.owner_id.is_(None),
        DBPrivateAIKey.litellm_token.isnot(None)
    ).count()

    user_key_count = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.team_id == team_id,
        DBPrivateAIKey.owner_id.isnot(None),
        DBPrivateAIKey.litellm_token.isnot(None)
    ).count()

    return {
        'total_keys': total_keys,
        'service_key_count': service_key_count,
        'user_key_count': user_key_count
    }


def test_worker_service_key_counting_bug_fix(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys and 2 user keys
    When: set_team_and_user_limits is called
    Then: It should count 77 service keys, not 79 (which would include user keys)
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['total_keys'] == 79  # 77 service + 2 user keys
    assert counts['service_key_count'] == 77
    assert counts['user_key_count'] == 2

    # Call the function that was fixed
    set_team_and_user_limits(db, team_data['team'])

    # Check what current_value was set for service keys
    service_key_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_data['team'].id,
        DBLimitedResource.resource == ResourceType.SERVICE_KEY
    ).first()

    assert service_key_limit is not None

    # This should be 77, not 79 (which would include user keys)
    assert service_key_limit.current_value == 77, f"Expected 77 service keys, got {service_key_limit.current_value}"


def test_check_key_limits_corrects_existing_incorrect_count(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys but a limit showing 150
    When: check_key_limits is called
    Then: The count should be corrected to 77, then incremented to 78
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77

    # Create an existing limit with an incorrect high count (simulating the production bug)
    existing_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=150.0,
        current_value=150.0,  # Incorrectly high count
        owner_type=OwnerType.TEAM,
        owner_id=team_data['team'].id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(existing_limit)
    db.commit()

    limit_service = LimitService(db)

    # The fix should ensure that check_key_limits corrects the count before trying to increment
    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"check_key_limits should work after the fix: {e}")

    # Check what happened to the count
    db.refresh(existing_limit)

    # After the fix, the count should be corrected to 77, then incremented to 78
    assert existing_limit.current_value == 78, f"Expected count to be corrected to 77 then incremented to 78, got {existing_limit.current_value}"


def test_check_key_limits_works_with_correct_count(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys and a limit showing 77
    When: check_key_limits is called
    Then: It should work correctly and increment to 78
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77

    # Create an existing limit with the correct count
    existing_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=150.0,
        current_value=77.0,  # Correct count
        owner_type=OwnerType.TEAM,
        owner_id=team_data['team'].id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(existing_limit)
    db.commit()

    limit_service = LimitService(db)

    # This should work correctly - increment from 77 to 78
    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"check_key_limits should work with correct count: {e}")

    # Check what happened to the count
    db.refresh(existing_limit)

    # The count should be incremented to 78
    assert existing_limit.current_value == 78, f"Expected count to be incremented to 78, got {existing_limit.current_value}"


def test_production_scenario_multiple_check_key_limits_calls(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys and 2 user keys
    When: check_key_limits is called multiple times
    Then: The count should not keep incrementing beyond the actual number of keys
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77

    limit_service = LimitService(db)

    # Simulate multiple calls to check_key_limits (as might happen in production)
    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"First call failed: {e}")

    # Check the count after first call
    service_key_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_data['team'].id,
        DBLimitedResource.resource == ResourceType.SERVICE_KEY
    ).first()

    if service_key_limit:
        first_call_count = service_key_limit.current_value
    else:
        pytest.fail("No service key limit found after first call")

    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"Second call failed: {e}")

    # Check the count after second call
    db.refresh(service_key_limit)
    if service_key_limit:
        second_call_count = service_key_limit.current_value
    else:
        pytest.fail("No service key limit found after second call")

    # The count should not keep incrementing beyond the actual number of keys
    # With the fix, the count should stabilize at 77 + number of actual calls that create keys
    assert second_call_count <= 80, f"Count is too high: {second_call_count}. This indicates the production bug where counts keep incrementing."


def test_production_scenario_existing_limit_with_high_count(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys but a limit showing 150
    When: check_key_limits is called
    Then: The count should be corrected to 77, then incremented to 78
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77

    # Create an existing limit with an incorrect high count (simulating the production bug)
    existing_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=150.0,
        current_value=150.0,  # Incorrectly high count
        owner_type=OwnerType.TEAM,
        owner_id=team_data['team'].id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    db.add(existing_limit)
    db.commit()

    limit_service = LimitService(db)

    # Now call check_key_limits - this should correct the count to 77, then increment to 78
    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"check_key_limits call failed: {e}")

    # Check what happened to the count
    db.refresh(existing_limit)

    # The count should be corrected to 77, then incremented to 78 (not incremented to 151)
    assert existing_limit.current_value == 78, f"Expected count to be corrected to 77 then incremented to 78, got {existing_limit.current_value}. This indicates the production bug is fixed."


def test_validation_methods_work_correctly(db, test_team_with_keys_and_users):
    """
    Given: A team with incorrect counts in both service and user key limits
    When: The validation methods are called
    Then: Both counts should be corrected to match the actual database counts
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77
    assert counts['user_key_count'] == 2

    # Create incorrect limits for both service keys and user keys
    service_key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.SERVICE_KEY,
        unit=UnitType.COUNT,
        max_value=150.0,
        current_value=100.0,  # Incorrect - should be 77
        owner_type=OwnerType.TEAM,
        owner_id=team_data['team'].id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )

    user_key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER_KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=10.0,  # Incorrect - should be 1
        owner_type=OwnerType.USER,
        owner_id=team_data['user1'].id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )

    db.add(service_key_limit)
    db.add(user_key_limit)
    db.commit()

    limit_service = LimitService(db)

    # Test the validation methods directly
    limit_service._validate_and_correct_service_key_count(team_data['team'].id)
    limit_service._validate_and_correct_user_key_count(team_data['user1'].id)

    # Check that the counts were corrected
    db.refresh(service_key_limit)
    db.refresh(user_key_limit)

    assert service_key_limit.current_value == 77, f"Expected service key count to be corrected to 77, got {service_key_limit.current_value}"
    assert user_key_limit.current_value == 1, f"Expected user key count to be corrected to 1, got {user_key_limit.current_value}"


def test_check_key_limits_creates_limit_with_correct_count(db, test_team_with_keys_and_users):
    """
    Given: A team with 77 service keys, and no existing limit
    When: check_key_limits is called for the creation of a new service key
    Then: the limit is created with the correct current_value
    """
    team_data = test_team_with_keys_and_users
    counts = get_actual_counts(db, team_data['team'].id)

    # Verify the test data setup is correct
    assert counts['service_key_count'] == 77

    # Verify no existing limit exists
    existing_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_data['team'].id,
        DBLimitedResource.resource == ResourceType.SERVICE_KEY
    ).first()
    assert existing_limit is None, "No existing limit should exist for this test"

    limit_service = LimitService(db)

    # Call check_key_limits - this should create a new limit with correct count
    try:
        limit_service.check_key_limits(team_data['team'].id, owner_id=None)
    except Exception as e:
        pytest.fail(f"check_key_limits should work and create a limit: {e}")

    # Check that a limit was created with the correct count
    new_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_data['team'].id,
        DBLimitedResource.resource == ResourceType.SERVICE_KEY
    ).first()

    assert new_limit is not None, "A new limit should have been created"

    # The limit should be created with the correct count (77) then incremented to 78
    assert new_limit.current_value == 78, f"Expected limit to be created with count 77 then incremented to 78, got {new_limit.current_value}"


def test_worker_vector_db_counting_bug_fix(db, test_team_with_keys_and_users):
    """
    Given: A team with vector DBs on both team-owned and user-owned keys
    When: set_team_and_user_limits is called
    Then: It should count only team-owned vector DBs, not all vector DBs
    """
    team_data = test_team_with_keys_and_users

    # Get team-owned keys (service keys) and user-owned keys separately
    team_keys = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.team_id == team_data['team'].id,
        DBPrivateAIKey.owner_id.is_(None)
    ).limit(3).all()  # First 3 service keys

    user_keys = db.query(DBPrivateAIKey).filter(
        DBPrivateAIKey.team_id == team_data['team'].id,
        DBPrivateAIKey.owner_id.isnot(None)
    ).limit(2).all()  # First 2 user keys

    # Make 3 team-owned keys have vector DBs
    for i, key in enumerate(team_keys):
        key.database_username = f"team_db_user_{i}"
        key.database_name = f"team_db_{i}"

    # Make 2 user-owned keys have vector DBs
    for i, key in enumerate(user_keys):
        key.database_username = f"user_db_user_{i}"
        key.database_name = f"user_db_{i}"

    db.commit()

    # Call the function
    set_team_and_user_limits(db, team_data['team'])

    # Check what current_value was set for vector DBs
    vector_db_limit = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_data['team'].id,
        DBLimitedResource.resource == ResourceType.VECTOR_DB
    ).first()

    assert vector_db_limit is not None

    # This should be 3 (team-owned only), not 5 (which would include user-owned)
    assert vector_db_limit.current_value == 3, f"Expected 3 team-owned vector DBs, got {vector_db_limit.current_value}. This indicates the vector_db double counting bug is fixed."