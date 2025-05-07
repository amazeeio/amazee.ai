import boto3
from botocore.exceptions import ClientError
from typing import Dict, Any, Callable
from datetime import datetime, timedelta, UTC
import os

_credentials_map: Dict[str, Dict[str, Any]] = {}
_region_name: str = os.getenv('AWS_REGION')

def _get_account_id(region_name: str) -> str:
    """
    Get the AWS account ID using GetCallerIdentity.

    Returns:
        str: The AWS account ID

    Raises:
        Exception: If unable to get the account ID
    """
    try:
        _region_name = region_name
        sts_client = boto3.client('sts', region_name=_region_name)
        response = sts_client.get_caller_identity()
        return response['Account']
    except ClientError as e:
        raise Exception(f"Failed to get account ID: {str(e)}")

def _check_credentials(role_name: str) -> None:
    """
    Check if credentials are valid and refresh if necessary.
    Raises an exception if credentials cannot be refreshed.
    """
    if role_name not in _credentials_map:
        _assume_role(role_name)
        return

    credentials = _credentials_map[role_name]
    expiry = credentials['Expiration']

    # Refresh if credentials expire in less than 5 minutes
    if datetime.now(UTC) + timedelta(minutes=5) >= expiry:
        _assume_role(role_name)

def _assume_role(role_name: str, session_name: str = "AWSServiceSession") -> None:
    """
    Assume the specified IAM role and get temporary credentials.
    Updates the credentials map with the new credentials.

    Args:
        session_name (str): Name for the assumed role session

    Raises:
        Exception: If role assumption fails
    """
    try:
        # Get account ID and construct role ARN
        account_id = _get_account_id(_region_name)
        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

        # Create STS client with default credentials
        sts_client = boto3.client('sts', region_name=_region_name)

        # Assume the role
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName=session_name
        )

        # Store credentials and expiry
        credentials = response['Credentials']
        _credentials_map[role_name] = {
            'AccessKeyId': credentials['AccessKeyId'],
            'SecretAccessKey': credentials['SecretAccessKey'],
            'SessionToken': credentials['SessionToken'],
            'Expiration': credentials['Expiration'].replace(tzinfo=UTC)
        }

    except ClientError as e:
        raise Exception(f"Failed to assume role: {str(e)}")

def get_credentials(role_name: str) -> Dict[str, str]:
    """
    Get the current AWS credentials.

    Returns:
        Dict[str, str]: Dictionary containing AccessKeyId, SecretAccessKey, and SessionToken
    """
    _check_credentials(role_name)
    credentials = _credentials_map[role_name]
    return {
        'aws_access_key_id': credentials['AccessKeyId'],
        'aws_secret_access_key': credentials['SecretAccessKey'],
        'aws_session_token': credentials['SessionToken']
    }

def ensure_valid_credentials(role_name: str) -> Callable:
    """
    Decorator to ensure valid credentials before executing AWS operations.

    Args:
        func (Callable): The function to decorate

    Returns:
        Callable: The decorated function
    """
    def inner(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            _check_credentials(role_name)
            return func(*args, **kwargs)
        return wrapper
    return inner