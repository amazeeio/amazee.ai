from app.services.aws_auth import _check_credentials, _assume_role, get_credentials
from datetime import datetime, timedelta, UTC
import pytest

def test_check_credentials_refresh(mock_sts_client):
    """Test that credentials are refreshed when they are about to expire."""
    # Set up test data
    role_name = "test-role"
    expiry = datetime.now(UTC) + timedelta(minutes=4)

    # Mock the credentials map with expiring credentials
    from app.services.aws_auth import _credentials_map
    _credentials_map[role_name] = {
        'AccessKeyId': 'old-key',
        'SecretAccessKey': 'old-secret',
        'SessionToken': 'old-token',
        'Expiration': expiry
    }

    # Call _check_credentials
    _check_credentials(role_name)

    # Verify assume_role was called again
    mock_sts_client.assume_role.assert_called()

def test_check_credentials_no_refresh(mock_sts_client):
    """Test that credentials are not refreshed when they are still valid."""
    # Set up test data
    role_name = "test-role"
    expiry = datetime.now(UTC) + timedelta(minutes=6)

    # Mock the credentials map with valid credentials
    from app.services.aws_auth import _credentials_map
    _credentials_map[role_name] = {
        'AccessKeyId': 'old-key',
        'SecretAccessKey': 'old-secret',
        'SessionToken': 'old-token',
        'Expiration': expiry
    }

    # Store initial assume_role call count
    initial_call_count = mock_sts_client.assume_role.call_count

    # Call _check_credentials
    _check_credentials(role_name)

    # Verify assume_role was not called again
    assert mock_sts_client.assume_role.call_count == initial_call_count

def test_check_credentials_no_expiry(mock_sts_client):
    """Test that credentials are refreshed when no expiry is set."""
    # Set up test data
    role_name = "test-role"

    # Mock the credentials map with no credentials
    from app.services.aws_auth import _credentials_map
    _credentials_map.clear()

    # Call _check_credentials
    _check_credentials(role_name)

    # Verify assume_role was called
    mock_sts_client.assume_role.assert_called()

def test_check_credentials_expired(mock_sts_client):
    """Test that credentials are refreshed when they have already expired."""
    # Set up test data
    role_name = "test-role"
    expiry = datetime.now(UTC) - timedelta(minutes=1)

    # Mock the credentials map with expired credentials
    from app.services.aws_auth import _credentials_map
    _credentials_map[role_name] = {
        'AccessKeyId': 'old-key',
        'SecretAccessKey': 'old-secret',
        'SessionToken': 'old-token',
        'Expiration': expiry
    }

    # Call _check_credentials
    _check_credentials(role_name)

    # Verify assume_role was called again
    mock_sts_client.assume_role.assert_called()

def test_get_credentials(mock_sts_client):
    """Test that get_credentials returns the correct credential format."""
    # Set up test data
    role_name = "test-role"
    test_credentials = {
        'AccessKeyId': 'test-key',
        'SecretAccessKey': 'test-secret',
        'SessionToken': 'test-token',
        'Expiration': datetime.now(UTC) + timedelta(hours=1)
    }

    # Mock the credentials map
    from app.services.aws_auth import _credentials_map
    _credentials_map[role_name] = test_credentials

    # Get credentials
    credentials = get_credentials(role_name)

    # Verify the format matches expected
    assert credentials == {
        'aws_access_key_id': test_credentials['AccessKeyId'],
        'aws_secret_access_key': test_credentials['SecretAccessKey'],
        'aws_session_token': test_credentials['SessionToken']
    }
