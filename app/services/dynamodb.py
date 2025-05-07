import boto3
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, UTC
import os
from app.services.aws_auth import ensure_valid_credentials, _region_name, get_credentials

VALIDATION_CODE_TABLE_NAME = "verification_codes"
# Get role name from environment variable
role_name = os.getenv('DYNAMODB_ROLE_NAME')

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

        if not _region_name:
            raise ValueError(
                "AWS_REGION environment variable is not set. "
                "Please set it to your AWS region (e.g., us-east-1)."
            )

        # Create DynamoDB resource with temporary credentials
        self.dynamodb = boto3.resource(
            'dynamodb',
            region_name=_region_name,
            **get_credentials(role_name)
        )

    @ensure_valid_credentials(role_name=role_name)
    def create_validation_code_table(self) -> bool:
        """
        Create a DynamoDB table for storing verification codes.
        The table will have email as the partition key.

        Returns:
            bool: True if table creation was successful, False otherwise.
        """
        try:
            table = self.dynamodb.create_table(
                TableName=VALIDATION_CODE_TABLE_NAME,
                KeySchema=[
                    {
                        'AttributeName': 'email',
                        'KeyType': 'HASH'  # Partition key
                    }
                ],
                AttributeDefinitions=[
                    {
                        'AttributeName': 'email',
                        'AttributeType': 'S'  # String type
                    }
                ],
                BillingMode='PAY_PER_REQUEST'
            )

            # Wait for table to be created
            table.meta.client.get_waiter('table_exists').wait(TableName=VALIDATION_CODE_TABLE_NAME)

            # Enable TTL after table creation
            self._enable_ttl()

            return True

        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceInUseException':
                # Table already exists, ensure TTL is enabled
                self._enable_ttl()
                return True
            raise e

    def _enable_ttl(self) -> None:
        """
        Enable TTL on the validation code table with a 10-minute expiration.
        """
        try:
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            table.meta.client.update_time_to_live(
                TableName=VALIDATION_CODE_TABLE_NAME,
                TimeToLiveSpecification={
                    'Enabled': True,
                    'AttributeName': 'ttl'
                }
            )
        except ClientError as e:
            print(f"Warning: Failed to enable TTL: {str(e)}")

    @ensure_valid_credentials(role_name=role_name)
    def verify_validation_code_table(self) -> bool:
        """
        Verify that the validation code table exists and is accessible.

        Returns:
            bool: True if table exists and is accessible, False otherwise.
        """
        try:
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            table.table_status
            return True
        except ClientError:
            return False

    @ensure_valid_credentials(role_name=role_name)
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
            print(f"Error writing record: {str(e)}")
            return False

    @ensure_valid_credentials(role_name=role_name)
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
