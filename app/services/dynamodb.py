import boto3
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, UTC
import os
import logging
from app.services.aws_auth import ensure_valid_credentials, get_credentials

# Set up logging
logger = logging.getLogger(__name__)

env_suffix = os.getenv('ENV_SUFFIX')
VALIDATION_CODE_TABLE_NAME = f"verification-codes-{env_suffix}"
# Get role name from environment variable
role_name = os.getenv('DYNAMODB_ROLE_NAME')
dynamodb_region = os.getenv('DYNAMODB_REGION', "eu-central-2")

class DynamoDBService:
    def __init__(
        self,
        session_name: str = "DynamoDBServiceSession"
    ):
        """
        Initialize the DynamoDB service with temporary credentials from STS AssumeRole.

        Args:
            session_name (str): Name for the assumed role session. Defaults to "DynamoDBServiceSession".

        Raises:
            ValueError: If DYNAMODB_ROLE_NAME or AWS_REGION environment variables are not set
        """
        if not role_name:
            raise ValueError(
                "DYNAMODB_ROLE_NAME environment variable is not set. "
                "Please set it to the name of the IAM role to assume."
            )

        if not dynamodb_region:
            raise ValueError(
                "DYNAMODB_REGION environment variable is not set. "
                "Please set it to your AWS region (e.g., eu-central-2)."
            )

        # Create DynamoDB resource with temporary credentials
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=dynamodb_region,
            **get_credentials(role_name, dynamodb_region)
        )

    @ensure_valid_credentials(role_name=role_name, region_name=dynamodb_region)
    def write_validation_code(self, email: str, code: str) -> bool:
        """
        Write or update a verification code record to the table.
        If a record with the same email exists, it will be updated with the new code and TTL.

        Args:
            email (str): Email address to store the code for
            code (str): Verification code to store

        Returns:
            bool: True if write/update was successful, False otherwise
        """
        try:
            # Calculate TTL (10 minutes from now)
            ttl = int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())

            # PutItem will create a new item or replace an existing one
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            table.put_item(
                Item={
                    'email': email,
                    'code': code,
                    'ttl': ttl,
                    'updated_at': datetime.now(UTC).isoformat()  # Track when the code was last updated
                }
            )
            return True
        except ClientError as e:
            logger.error(f"Error writing record: {str(e)}")
            return False

    @ensure_valid_credentials(role_name=role_name, region_name=dynamodb_region)
    def read_validation_code(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Read a verification code record from the table.

        Args:
            email (str): Email address to look up

        Returns:
            Optional[Dict[str, Any]]: The record if found, None otherwise
        """
        try:
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            response = table.get_item(
                Key={
                    'email': email
                }
            )
            return response.get('Item')
        except ClientError:
            return None
