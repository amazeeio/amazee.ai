import pytest
from datetime import datetime, UTC
from unittest.mock import Mock, patch
from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBLimitedResource
from app.schemas.limits import ResourceType, UnitType, OwnerType, LimitSource, LimitType
from app.core.worker import set_team_and_user_limits


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_sets_team_limits(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with users and existing limits
    When: Calling set_team_and_user_limits with the team
    Then: Team limits are set and user key limits are set for all users in the team
    """
    # Create a second user in the team
    second_user = DBUser(
        email="second@test.com",
        team_id=test_team.id,
        role="member",
        created_at=datetime.now(UTC)
    )
    db.add(second_user)
    db.commit()

    # Create some existing limits that should be updated
    team_user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    team_key_limit = DBLimitedResource(
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.SERVICE_KEY,
            unit=UnitType.COUNT,
            max_value=10.0,
            current_value=0.0,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.DEFAULT,
            created_at=datetime.now(UTC)
        )
    db.add(team_user_limit)
    db.add(team_key_limit)
    db.commit()

    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()
    mock_limit_service.get_team_limits = Mock(return_value=[
        Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0),
        Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0),
        Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)
    ])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify team limits were set
    mock_limit_service.set_team_limits.assert_called_once_with(test_team)

    # Verify user limits were set for both users
    assert mock_limit_service.set_user_limits.call_count == 2
    mock_limit_service.set_user_limits.assert_any_call(test_team_user)
    mock_limit_service.set_user_limits.assert_any_call(second_user)


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_updates_current_values(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with users and keys
    When: Calling set_team_and_user_limits with the team
    Then: Current values are updated based on actual counts in the database
    """
    # Create a second user in the team
    second_user = DBUser(
        email="second@test.com",
        team_id=test_team.id,
        role="member",
        created_at=datetime.now(UTC)
    )
    db.add(second_user)
    db.commit()

    # Create some keys for the team and users
    team_key = DBPrivateAIKey(
        name="Team Key",
        team_id=test_team.id,
        litellm_token="team_token_123",
        created_at=datetime.now(UTC)
    )
    user_key = DBPrivateAIKey(
        name="User Key",
        owner_id=test_team_user.id,
        team_id=test_team.id,
        litellm_token="user_token_123",
        created_at=datetime.now(UTC)
    )
    db.add(team_key)
    db.add(user_key)
    db.commit()

    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock team limits that need current value updates
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0)
    vector_db_limit = Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)

    # Create mock user limits that need current value updates
    user_key_limit = Mock(resource=ResourceType.USER_KEY, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, key_limit, vector_db_limit])
    mock_limit_service.get_user_limits = Mock(return_value=[user_key_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify current values were set correctly
    # Should be called for team limits: USER (2 users), KEY (2 keys), VECTOR_DB (0 vector dbs)
    # Plus user limits: KEY (1 key per user) for each user
    assert mock_limit_service.set_current_value.call_count == 5  # 3 team + 2 user calls

    # Check that set_current_value was called with correct values
    calls = mock_limit_service.set_current_value.call_args_list

    # Find the calls for each resource type (team limits)
    team_user_call = next(call for call in calls if call[0][0].resource == ResourceType.USER)
    team_key_call = next(call for call in calls if call[0][0].resource == ResourceType.SERVICE_KEY)
    vector_db_call = next(call for call in calls if call[0][0].resource == ResourceType.VECTOR_DB)

    # Verify the team limit counts are correct
    assert team_user_call[0][1] == 2  # 2 users in team
    assert team_key_call[0][1] == 2   # 2 keys total (1 team + 1 user)
    assert vector_db_call[0][1] == 0  # 0 vector dbs

    # Verify user key limits were updated (should be called twice, once per user)
    # We expect 5 total calls: 3 team limits + 2 user limits
    # The user key calls should have values 1 and 0 (one user has 1 key, the other has 0)
    user_key_calls = [call for call in calls if call[0][0].resource == ResourceType.USER_KEY]
    # We should have 2 USER_KEY calls total: 1 per user
    assert len(user_key_calls) == 2

    # Check that we have the expected counts: 1 (user 1), 0 (user 2)
    key_counts = [call[0][1] for call in user_key_calls]
    assert 1 in key_counts  # First user has 1 key
    assert 0 in key_counts  # Second user has 0 keys


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_handles_team_with_no_users(mock_limit_service_class, db, test_team):
    """
    Given: A team with no users
    When: Calling set_team_and_user_limits with the team
    Then: Team limits are set but no user limits are set
    """
    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()
    mock_limit_service.get_team_limits = Mock(return_value=[])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify team limits were set
    mock_limit_service.set_team_limits.assert_called_once_with(test_team)

    # Verify no user limits were set
    mock_limit_service.set_user_limits.assert_not_called()


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_handles_team_with_no_keys(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with users but no keys
    When: Calling set_team_and_user_limits with the team
    Then: Current values are set to 0 for key-related limits
    """
    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock limits that need current value updates
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0)
    vector_db_limit = Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, key_limit, vector_db_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify current values were set correctly
    calls = mock_limit_service.set_current_value.call_args_list

    # Find the calls for each resource type
    user_call = next(call for call in calls if call[0][0].resource == ResourceType.USER)
    key_call = next(call for call in calls if call[0][0].resource == ResourceType.SERVICE_KEY)
    vector_db_call = next(call for call in calls if call[0][0].resource == ResourceType.VECTOR_DB)

    # Verify the counts are correct
    assert user_call[0][1] == 1  # 1 user in team
    assert key_call[0][1] == 0   # 0 keys
    assert vector_db_call[0][1] == 0  # 0 vector dbs


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_skips_non_count_limits(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with limits that are not COUNT type
    When: Calling set_team_and_user_limits with the team
    Then: Non-COUNT limits are skipped and not updated
    """
    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock limits - some COUNT, some not
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    budget_limit = Mock(resource=ResourceType.BUDGET, unit=UnitType.DOLLAR, current_value=0.0)  # Not COUNT
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, budget_limit, key_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify only COUNT limits had their current values updated
    calls = mock_limit_service.set_current_value.call_args_list
    assert len(calls) == 2  # Only USER and KEY limits, not BUDGET

    # Verify the correct limits were updated
    resources_updated = [call[0][0].resource for call in calls]
    assert ResourceType.USER in resources_updated
    assert ResourceType.SERVICE_KEY in resources_updated
    assert ResourceType.BUDGET not in resources_updated


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_skips_limits_with_non_zero_current_value(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with limits that already have non-zero current values
    When: Calling set_team_and_user_limits with the team
    Then: Limits with non-zero current values are skipped
    """
    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock limits - some with zero current value, some with non-zero
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=5.0)  # Non-zero
    vector_db_limit = Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, key_limit, vector_db_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify only limits with zero current values were updated
    calls = mock_limit_service.set_current_value.call_args_list
    assert len(calls) == 2  # Only USER and VECTOR_DB limits, not KEY

    # Verify the correct limits were updated
    resources_updated = [call[0][0].resource for call in calls]
    assert ResourceType.USER in resources_updated
    assert ResourceType.VECTOR_DB in resources_updated
    assert ResourceType.SERVICE_KEY not in resources_updated


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_handles_vector_db_counting(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with keys that have database_username set
    When: Calling set_team_and_user_limits with the team
    Then: Vector DB count is calculated correctly based on keys with database_username
    """
    # Create keys - some with database_username, some without
    key_with_db = DBPrivateAIKey(
        name="Key with DB",
        team_id=test_team.id,
        litellm_token="token_1",
        database_username="db_user",
        created_at=datetime.now(UTC)
    )
    key_without_db = DBPrivateAIKey(
        name="Key without DB",
        team_id=test_team.id,
        litellm_token="token_2",
        database_username=None,
        created_at=datetime.now(UTC)
    )
    db.add(key_with_db)
    db.add(key_without_db)
    db.commit()

    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock limits
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0)
    vector_db_limit = Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, key_limit, vector_db_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify vector DB count was set correctly
    calls = mock_limit_service.set_current_value.call_args_list
    vector_db_call = next(call for call in calls if call[0][0].resource == ResourceType.VECTOR_DB)
    assert vector_db_call[0][1] == 1  # Only 1 key has database_username


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_handles_key_counting_with_litellm_token(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with keys - some with litellm_token, some without
    When: Calling set_team_and_user_limits with the team
    Then: Key count only includes keys with litellm_token
    """
    # Create keys - some with litellm_token, some without
    key_with_token = DBPrivateAIKey(
        name="Key with token",
        team_id=test_team.id,
        litellm_token="token_1",
        created_at=datetime.now(UTC)
    )
    key_without_token = DBPrivateAIKey(
        name="Key without token",
        team_id=test_team.id,
        litellm_token=None,
        created_at=datetime.now(UTC)
    )
    db.add(key_with_token)
    db.add(key_without_token)
    db.commit()

    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock limits
    user_limit = Mock(resource=ResourceType.USER, unit=UnitType.COUNT, current_value=0.0)
    key_limit = Mock(resource=ResourceType.SERVICE_KEY, unit=UnitType.COUNT, current_value=0.0)
    vector_db_limit = Mock(resource=ResourceType.VECTOR_DB, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[user_limit, key_limit, vector_db_limit])
    mock_limit_service.get_user_limits = Mock(return_value=[])  # No user limits for this test
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify key count only includes keys with litellm_token
    calls = mock_limit_service.set_current_value.call_args_list
    key_call = next(call for call in calls if call[0][0].resource == ResourceType.SERVICE_KEY)
    assert key_call[0][1] == 1  # Only 1 key has litellm_token


@patch('app.core.worker.LimitService')
def test_set_team_and_user_limits_updates_user_key_current_values(mock_limit_service_class, db, test_team, test_team_user):
    """
    Given: A team with users that have different numbers of keys
    When: Calling set_team_and_user_limits with the team
    Then: Each user's key limit current_value is updated with their actual key count
    """
    # Create a second user in the team
    second_user = DBUser(
        email="second@test.com",
        team_id=test_team.id,
        role="member",
        created_at=datetime.now(UTC)
    )
    db.add(second_user)
    db.commit()

    # Create keys for each user - different counts
    # First user has 2 keys
    user1_key1 = DBPrivateAIKey(
        name="User 1 Key 1",
        owner_id=test_team_user.id,
        team_id=test_team.id,
        litellm_token="user1_token_1",
        created_at=datetime.now(UTC)
    )
    user1_key2 = DBPrivateAIKey(
        name="User 1 Key 2",
        owner_id=test_team_user.id,
        team_id=test_team.id,
        litellm_token="user1_token_2",
        created_at=datetime.now(UTC)
    )

    # Second user has 1 key
    user2_key1 = DBPrivateAIKey(
        name="User 2 Key 1",
        owner_id=second_user.id,
        team_id=test_team.id,
        litellm_token="user2_token_1",
        created_at=datetime.now(UTC)
    )

    db.add(user1_key1)
    db.add(user1_key2)
    db.add(user2_key1)
    db.commit()

    # Mock the limit service
    mock_limit_service = mock_limit_service_class.return_value
    mock_limit_service.set_team_limits = Mock()
    mock_limit_service.set_user_limits = Mock()

    # Create mock user limits that need current value updates
    user_key_limit = Mock(resource=ResourceType.USER_KEY, unit=UnitType.COUNT, current_value=0.0)

    mock_limit_service.get_team_limits = Mock(return_value=[])  # No team limits for this test
    mock_limit_service.get_user_limits = Mock(return_value=[user_key_limit])
    mock_limit_service.set_current_value = Mock()

    # Call the function
    set_team_and_user_limits(db, test_team)

    # Verify user key limits were updated with correct counts
    calls = mock_limit_service.set_current_value.call_args_list
    assert len(calls) == 2  # One call per user

    # The calls should be for user key limits with counts 2 and 1
    call_counts = [call[0][1] for call in calls]
    assert 2 in call_counts  # First user has 2 keys
    assert 1 in call_counts  # Second user has 1 key
