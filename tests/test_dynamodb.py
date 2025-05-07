import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, UTC
import os
from app.services.dynamodb import DynamoDBService

@pytest.fixture
def mock_env_vars():
    """Fixture to set required environment variables for testing."""
    with patch.dict(os.environ, {
        'DYNAMODB_ROLE_NAME': 'test-role',
        'AWS_REGION': 'us-east-1'
    }):
        yield

@pytest.fixture
def mock_sts_client():
    """Fixture to mock STS client responses."""
    with patch('boto3.client') as mock_client:
        # Mock get_caller_identity response
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {'Account': '123456789012'}

        # Mock assume_role response
        mock_sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'test-access-key',
                'SecretAccessKey': 'test-secret-key',
                'SessionToken': 'test-session-token',
                'Expiration': datetime.now(UTC) + timedelta(hours=1)
            }
        }

        mock_client.return_value = mock_sts
        yield mock_sts

def test_init_missing_role_name():
    """Test initialization fails when DYNAMODB_ROLE_NAME is not set."""
    with patch.dict(os.environ, {'AWS_REGION': 'us-east-1'}, clear=True):
        with pytest.raises(ValueError, match="DYNAMODB_ROLE_NAME environment variable is not set"):
            DynamoDBService()

def test_init_missing_region():
    """Test initialization fails when AWS_REGION is not set."""
    with patch.dict(os.environ, {'DYNAMODB_ROLE_NAME': 'test-role'}, clear=True):
        with pytest.raises(ValueError, match="AWS_REGION environment variable is not set"):
            DynamoDBService()

def test_init_success(mock_env_vars, mock_sts_client):
    """Test successful initialization with valid environment variables."""
    service = DynamoDBService()
    assert service.role_name == 'test-role'
    assert service.region_name == 'us-east-1'
    assert service.credentials_expiry is not None
    assert service.dynamodb is not None

def test_check_credentials_refresh(mock_env_vars, mock_sts_client):
    """Test that credentials are refreshed when they are about to expire."""
    service = DynamoDBService()

    # Set credentials to expire in 4 minutes (less than 5-minute threshold)
    service.credentials_expiry = datetime.now(UTC) + timedelta(minutes=4)

    # Call _check_credentials
    service._check_credentials()

    # Verify assume_role was called again
    mock_sts_client.assume_role.assert_called()

def test_check_credentials_no_refresh(mock_env_vars, mock_sts_client):
    """Test that credentials are not refreshed when they are still valid."""
    service = DynamoDBService()

    # Set credentials to expire in 6 minutes (more than 5-minute threshold)
    service.credentials_expiry = datetime.now(UTC) + timedelta(minutes=6)

    # Store initial assume_role call count
    initial_call_count = mock_sts_client.assume_role.call_count

    # Call _check_credentials
    service._check_credentials()

    # Verify assume_role was not called again
    assert mock_sts_client.assume_role.call_count == initial_call_count

def test_check_credentials_no_expiry(mock_env_vars, mock_sts_client):
    """Test that credentials are refreshed when no expiry is set."""
    service = DynamoDBService()
    service.credentials_expiry = None

    # Call _check_credentials
    service._check_credentials()

    # Verify assume_role was called
    mock_sts_client.assume_role.assert_called()

def test_check_credentials_expired(mock_env_vars, mock_sts_client):
    """Test that credentials are refreshed when they have already expired."""
    service = DynamoDBService()

    # Set credentials to have expired 1 minute ago
    service.credentials_expiry = datetime.now(UTC) - timedelta(minutes=1)

    # Call _check_credentials
    service._check_credentials()

    # Verify assume_role was called again
    mock_sts_client.assume_role.assert_called()