import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import Mock, patch
from sqlalchemy.orm import Session

from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBTeamProduct, DBProduct
from app.core.worker import _calculate_last_team_activity, _send_retention_warning, _check_team_retention_policy
from app.services.ses import SESService


def test_calculate_last_team_activity_with_product_association(db: Session, test_team):
    """
    Given: A team with a product association
    When: Calculating the last team activity
    Then: Should return current time indicating the team is active
    """
    # Create a product and associate it with the team
    product = DBProduct(
        id="test-product",
        name="Test Product",
        active=True
    )
    db.add(product)
    db.commit()

    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id,
        created_at=datetime.now(UTC) - timedelta(days=30)
    )
    db.add(team_product)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return current time since team has products (team is active)
    assert last_activity is not None
    # Should be very recent (within the last few seconds)
    assert (datetime.now(UTC) - last_activity).total_seconds() < 5


def test_calculate_last_team_activity_with_key_usage(db: Session, test_team, test_region):
    """
    Given: A team with a key that has recent usage (updated_at timestamp)
    When: Calculating the last team activity
    Then: Should return the key creation date (most recent activity)
    """
    # Create a key with recent update (indicating usage)
    recent_update_time = datetime.now(UTC) - timedelta(days=10)
    key = DBPrivateAIKey(
        name="test-key",
        litellm_token="test-token",
        team_id=test_team.id,
        region_id=test_region.id,
        updated_at=recent_update_time
    )
    db.add(key)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return the key creation date (most recent activity)
    assert last_activity == key.created_at


def test_calculate_last_team_activity_with_user_creation(db: Session, test_team):
    """
    Given: A team with a recently created user
    When: Calculating the last team activity
    Then: Should return the user creation date
    """
    # Create a user with recent creation
    recent_creation_time = datetime.now(UTC) - timedelta(days=20)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=recent_creation_time
    )
    db.add(user)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return the user creation date
    assert last_activity == recent_creation_time


def test_calculate_last_team_activity_with_key_creation(db: Session, test_team, test_region):
    """
    Given: A team with a recently created key
    When: Calculating the last team activity
    Then: Should return the key creation date
    """
    # Create a key with recent creation
    recent_creation_time = datetime.now(UTC) - timedelta(days=15)
    key = DBPrivateAIKey(
        name="test-key",
        litellm_token="test-token",
        team_id=test_team.id,
        region_id=test_region.id,
        created_at=recent_creation_time
    )
    db.add(key)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return the key creation date
    assert last_activity == recent_creation_time


def test_calculate_last_team_activity_returns_most_recent(db: Session, test_team, test_region):
    """
    Given: A team with multiple activities at different times
    When: Calculating the last team activity
    Then: Should return the most recent activity date
    """
    # Create multiple activities with different dates
    old_time = datetime.now(UTC) - timedelta(days=50)
    recent_time = datetime.now(UTC) - timedelta(days=5)

    # Old user
    old_user = DBUser(
        email="old@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(old_user)

    # Recent key
    recent_key = DBPrivateAIKey(
        name="recent-key",
        litellm_token="recent-token",
        team_id=test_team.id,
        region_id=test_region.id,
        updated_at=recent_time
    )
    db.add(recent_key)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return the most recent activity (key creation, since created_at is now)
    assert last_activity == recent_key.created_at


def test_calculate_last_team_activity_no_activity(db: Session, test_team):
    """
    Given: A team with no users, keys, or products
    When: Calculating the last team activity
    Then: Should return None
    """
    # Calculate last activity for team with no users, keys, or products
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return None since no activity found
    assert last_activity is None


def test_send_retention_warning_success(db: Session, test_team):
    """
    Given: A team that needs a retention warning and SES service is available
    When: Sending a retention warning email
    Then: Should send email successfully and update the team's warning timestamp
    """
    # Create a mock SES service
    mock_ses_service = Mock()
    mock_ses_service.send_email.return_value = True

    # Send retention warning
    _send_retention_warning(db, test_team, mock_ses_service)

    # Verify email was sent
    mock_ses_service.send_email.assert_called_once()

    # Verify team was updated with warning timestamp
    db.refresh(test_team)
    assert test_team.retention_warning_sent_at is not None


def test_send_retention_warning_failure(db: Session, test_team):
    """
    Given: A team that needs a retention warning but SES service fails
    When: Sending a retention warning email
    Then: Should not update the team's warning timestamp
    """
    # Create a mock SES service that returns failure
    mock_ses_service = Mock()
    mock_ses_service.send_email.return_value = False

    # Send retention warning
    _send_retention_warning(db, test_team, mock_ses_service)

    # Verify email was attempted
    mock_ses_service.send_email.assert_called_once()

    # Verify team was NOT updated
    db.refresh(test_team)
    assert test_team.retention_warning_sent_at is None


def test_send_retention_warning_no_ses_service(db: Session, test_team):
    """
    Given: A team that needs a retention warning but no SES service is available
    When: Sending a retention warning email
    Then: Should handle gracefully without errors
    """
    # Send retention warning without SES service
    _send_retention_warning(db, test_team, None)

    # Verify team was NOT updated
    db.refresh(test_team)
    assert test_team.retention_warning_sent_at is None


@patch('app.core.worker._send_retention_warning')
def test_check_team_retention_policy_warning_sent(mock_send_warning, db: Session, test_team):
    """
    Given: A team that has been inactive for 80 days
    When: Checking the team retention policy
    Then: Should send a retention warning
    """
    # Create a user 80 days ago (should trigger warning)
    old_time = datetime.now(UTC) - timedelta(days=80)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(user)
    db.commit()

    current_time = datetime.now(UTC)

    # Call the retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify warning was sent
    mock_send_warning.assert_called_once_with(db, test_team, None)


@patch('app.core.worker._send_retention_warning')
def test_check_team_retention_policy_team_deleted(mock_send_warning, db: Session, test_team):
    """
    Given: A team that has been inactive for 100 days and warning was sent 20 days ago
    When: Checking the team retention policy
    Then: Should soft-delete the team
    """
    # Create a user 100 days ago
    old_time = datetime.now(UTC) - timedelta(days=100)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(user)

    # Set warning sent 20 days ago (should trigger deletion)
    test_team.retention_warning_sent_at = datetime.now(UTC) - timedelta(days=20)
    db.commit()

    current_time = datetime.now(UTC)

    # Call the retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify team was soft-deleted
    db.refresh(test_team)
    assert test_team.deleted_at is not None

    # Verify warning was not sent (already sent)
    mock_send_warning.assert_not_called()


def test_check_team_retention_policy_already_deleted(db: Session, test_team):
    """
    Given: A team that is already soft-deleted
    When: Checking the team retention policy
    Then: Should skip processing and not make any changes
    """
    # Soft delete the team first
    test_team.deleted_at = datetime.now(UTC)
    db.commit()

    current_time = datetime.now(UTC)

    # Call the retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify no changes were made (team was already deleted)
    db.refresh(test_team)
    assert test_team.deleted_at is not None


@patch('app.core.worker._send_retention_warning')
def test_check_team_retention_policy_no_action_needed(mock_send_warning, db: Session, test_team):
    """
    Given: A team that has been active recently (10 days ago)
    When: Checking the team retention policy
    Then: Should not send warning or delete the team
    """
    # Create a user 10 days ago (should not trigger any action)
    recent_time = datetime.now(UTC) - timedelta(days=10)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=recent_time
    )
    db.add(user)
    db.commit()

    current_time = datetime.now(UTC)

    # Call the retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify no warning was sent and team was not deleted
    mock_send_warning.assert_not_called()
    db.refresh(test_team)
    assert test_team.deleted_at is None


@patch('app.core.worker._send_retention_warning')
def test_check_team_retention_policy_warning_already_sent(mock_send_warning, db: Session, test_team):
    """
    Given: A team that has been inactive for 80 days but warning was already sent
    When: Checking the team retention policy
    Then: Should not send another warning
    """
    # Set warning already sent
    test_team.retention_warning_sent_at = datetime.now(UTC) - timedelta(days=1)

    # Create a user 80 days ago (would normally trigger warning)
    old_time = datetime.now(UTC) - timedelta(days=80)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(user)
    db.commit()

    current_time = datetime.now(UTC)

    # Call the retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify warning was not sent again
    mock_send_warning.assert_not_called()


def test_list_teams_excludes_deleted_by_default(client, admin_token, test_team, db):
    """
    Given: A soft-deleted team in the database
    When: Listing teams without include_deleted parameter
    Then: Should not include the deleted team in the results
    """
    # Soft delete the team
    test_team.deleted_at = datetime.now(UTC)
    db.commit()

    response = client.get("/teams", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 0  # Should not include deleted team


def test_list_teams_includes_deleted_when_requested(client, admin_token, test_team, db):
    """
    Given: A soft-deleted team in the database
    When: Listing teams with include_deleted=true parameter
    Then: Should include the deleted team in the results
    """
    # Soft delete the team
    test_team.deleted_at = datetime.now(UTC)
    db.commit()

    response = client.get("/teams?include_deleted=true", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    teams = response.json()
    assert len(teams) == 1  # Should include deleted team
    assert teams[0]["id"] == test_team.id
    assert teams[0]["deleted_at"] is not None


def test_get_team_excludes_deleted_by_default(client, admin_token, test_team, db):
    """
    Given: A soft-deleted team in the database
    When: Getting a team without include_deleted parameter
    Then: Should return 404 Not Found
    """
    # Soft delete the team
    test_team.deleted_at = datetime.now(UTC)
    db.commit()

    response = client.get(f"/teams/{test_team.id}", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 404  # Should not find deleted team


def test_get_team_includes_deleted_for_admin(client, admin_token, test_team, db):
    """
    Given: A soft-deleted team in the database and an admin user
    When: Getting the team with include_deleted=true parameter
    Then: Should return the deleted team details
    """
    # Soft delete the team
    test_team.deleted_at = datetime.now(UTC)
    db.commit()

    response = client.get(f"/teams/{test_team.id}?include_deleted=true", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    team = response.json()
    assert team["id"] == test_team.id
    assert team["deleted_at"] is not None


def test_get_team_denies_deleted_access_for_non_admin(client, team_admin_token, test_team, db):
    """
    Given: A soft-deleted team in the database and a non-admin user
    When: Getting the team with include_deleted=true parameter
    Then: Should return 404 Not Found (team appears to not exist)
    """
    # Soft delete the team
    from app.db.models import DBTeam
    db.query(DBTeam).filter(DBTeam.id == test_team.id).update({"deleted_at": datetime.now(UTC)})
    db.commit()

    response = client.get(f"/teams/{test_team.id}?include_deleted=true", headers={"Authorization": f"Bearer {team_admin_token}"})

    assert response.status_code == 404  # Should be not found for non-admin (team appears to not exist)


def test_teams_with_products_never_deleted(db: Session, test_team):
    """
    Given: A team with product associations
    When: Checking retention policy
    Then: Team should never be considered for deletion regardless of other activity
    """
    # Create a product and associate it with the team
    product = DBProduct(
        id="test-product",
        name="Test Product",
        active=True
    )
    db.add(product)
    db.commit()

    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id,
        created_at=datetime.now(UTC) - timedelta(days=100)  # Very old
    )
    db.add(team_product)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return current time since team has products (team is considered active)
    assert last_activity is not None
    # Should be very recent (within the last few seconds)
    assert (datetime.now(UTC) - last_activity).total_seconds() < 5


def test_teams_with_recent_key_usage_not_deleted(db: Session, test_team, test_region):
    """
    Given: A team with recent key usage (within 90 days)
    When: Checking retention policy
    Then: Team should not be considered for deletion
    """
    # Create a key with recent usage
    recent_time = datetime.now(UTC) - timedelta(days=10)
    key = DBPrivateAIKey(
        name="test-key",
        litellm_token="test-token",
        team_id=test_team.id,
        region_id=test_region.id,
        updated_at=recent_time
    )
    db.add(key)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return recent activity (key creation since it's most recent)
    assert last_activity == key.created_at


def test_teams_with_recent_resource_creation_not_deleted(db: Session, test_team):
    """
    Given: A team with recent resource creation (within 90 days)
    When: Checking retention policy
    Then: Team should not be considered for deletion
    """
    # Create a user recently
    recent_time = datetime.now(UTC) - timedelta(days=5)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=recent_time
    )
    db.add(user)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return recent activity
    assert last_activity == recent_time


def test_warning_only_sent_once(db: Session, test_team):
    """
    Given: A team that has already received a retention warning
    When: Checking retention policy again
    Then: Should not send another warning
    """
    # Set warning already sent
    warning_time = datetime.now(UTC) - timedelta(days=1)
    test_team.retention_warning_sent_at = warning_time

    # Create old activity that would normally trigger warning
    old_time = datetime.now(UTC) - timedelta(days=80)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(user)
    db.commit()

    current_time = datetime.now(UTC)

    # Call retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify warning timestamp was not updated
    db.refresh(test_team)
    assert test_team.retention_warning_sent_at == warning_time


def test_soft_delete_timestamp_set(db: Session, test_team):
    """
    Given: A team that has been inactive for over 90 days and warning was sent 14 days ago
    When: Checking retention policy
    Then: Should set the deleted_at timestamp
    """
    # Create old activity
    old_time = datetime.now(UTC) - timedelta(days=100)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=old_time
    )
    db.add(user)

    # Set warning sent 14 days ago (should trigger deletion)
    test_team.retention_warning_sent_at = datetime.now(UTC) - timedelta(days=14)
    db.commit()

    current_time = datetime.now(UTC)

    # Call retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify team was soft-deleted
    db.refresh(test_team)
    assert test_team.deleted_at is not None


def test_team_activity_calculation_edge_cases(db: Session, test_team, test_region):
    """
    Given: A team with multiple types of activities at different times
    When: Calculating the last team activity
    Then: Should return the most recent activity across all types
    """
    # Create activities at different times
    very_old_time = datetime.now(UTC) - timedelta(days=100)
    old_time = datetime.now(UTC) - timedelta(days=50)
    recent_time = datetime.now(UTC) - timedelta(days=10)

    # Very old user
    old_user = DBUser(
        email="old@example.com",
        team_id=test_team.id,
        created_at=very_old_time
    )
    db.add(old_user)

    # Old key creation
    old_key = DBPrivateAIKey(
        name="old-key",
        litellm_token="old-token",
        team_id=test_team.id,
        region_id=test_region.id,
        created_at=old_time
    )
    db.add(old_key)

    # Recent key update
    recent_key = DBPrivateAIKey(
        name="recent-key",
        litellm_token="recent-token",
        team_id=test_team.id,
        region_id=test_region.id,
        updated_at=recent_time
    )
    db.add(recent_key)
    db.commit()

    # Calculate last activity
    last_activity = _calculate_last_team_activity(db, test_team)

    # Should return the most recent activity (recent key creation)
    assert last_activity == recent_key.created_at


def test_team_retention_policy_timing(db: Session, test_team):
    """
    Given: A team with activity at exactly 77 days ago
    When: Checking retention policy
    Then: Should send retention warning
    """
    # Create activity exactly 77 days ago (should trigger warning since >76 days)
    warning_time = datetime.now(UTC) - timedelta(days=77)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=warning_time
    )
    db.add(user)
    db.commit()

    current_time = datetime.now(UTC)

    # Call retention policy method
    with patch('app.core.worker._send_retention_warning') as mock_send_warning:
        _check_team_retention_policy(db, test_team, current_time, None)

        # Verify warning was sent
        mock_send_warning.assert_called_once()


def test_team_retention_policy_deletion_timing(db: Session, test_team):
    """
    Given: A team with activity at exactly 91 days ago and warning sent 14 days ago
    When: Checking retention policy
    Then: Should soft-delete the team
    """
    # Create activity exactly 91 days ago
    deletion_time = datetime.now(UTC) - timedelta(days=91)
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id,
        created_at=deletion_time
    )
    db.add(user)

    # Set warning sent exactly 14 days ago (should trigger deletion)
    test_team.retention_warning_sent_at = datetime.now(UTC) - timedelta(days=14)
    db.commit()

    current_time = datetime.now(UTC)

    # Call retention policy method
    _check_team_retention_policy(db, test_team, current_time, None)

    # Verify team was soft-deleted
    db.refresh(test_team)
    assert test_team.deleted_at is not None
