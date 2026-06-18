import boto3
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, UTC
import os
import logging
from app.services.aws_auth import ensure_valid_credentials, get_credentials

# Set up logging
logger = logging.getLogger(__name__)

env_suffix = os.getenv("ENV_SUFFIX")
VALIDATION_CODE_TABLE_NAME = f"verification-codes-{env_suffix}"
# Drupal pending attribution items use the same table with a prefixed key
_DRUPAL_PENDING_PREFIX = "drupal::"
# Pending attribution TTL: 15 minutes (long enough to survive email delivery + sign-in)
_DRUPAL_PENDING_TTL_MINUTES = 15
# Get role name from environment variable
role_name = os.getenv("DYNAMODB_ROLE_NAME")
dynamodb_region = os.getenv("DYNAMODB_REGION", "eu-central-2")


class DynamoDBService:
    def __init__(self, session_name: str = "DynamoDBServiceSession"):
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
            "dynamodb",
            region_name=dynamodb_region,
            **get_credentials(role_name, dynamodb_region),
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
                    "email": email,
                    "code": code,
                    "ttl": ttl,
                    "updated_at": datetime.now(
                        UTC
                    ).isoformat(),  # Track when the code was last updated
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
            response = table.get_item(Key={"email": email})
            return response.get("Item")
        except ClientError:
            return None

    @ensure_valid_credentials(role_name=role_name, region_name=dynamodb_region)
    def write_drupal_pending(self, email: str) -> bool:
        """
        Record a short-lived pending Drupal attribution marker for the given email.

        Called when POST /auth/validate-email is received so that a subsequent
        POST /auth/sign-in can promote it to a durable user flag.

        Args:
            email (str): Normalised (lowercased) email address

        Returns:
            bool: True if the write succeeded, False otherwise
        """
        try:
            ttl = int(
                (
                    datetime.now(UTC) + timedelta(minutes=_DRUPAL_PENDING_TTL_MINUTES)
                ).timestamp()
            )
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            table.put_item(
                Item={
                    "email": f"{_DRUPAL_PENDING_PREFIX}{email}",
                    "drupal_pending": True,
                    "ttl": ttl,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            return True
        except ClientError as e:
            logger.error(f"Error writing Drupal pending marker for {email}: {e}")
            return False

    @ensure_valid_credentials(role_name=role_name, region_name=dynamodb_region)
    def read_drupal_pending(self, email: str) -> bool:
        """
        Check whether a pending Drupal attribution marker exists for the given email.

        Args:
            email (str): Normalised (lowercased) email address

        Returns:
            bool: True if a non-expired marker is present, False otherwise
        """
        try:
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            response = table.get_item(Key={"email": f"{_DRUPAL_PENDING_PREFIX}{email}"})
            item = response.get("Item")
            if not item:
                return False
            # Respect TTL manually in case DynamoDB hasn't expired it yet
            ttl = item.get("ttl", 0)
            if ttl and int(datetime.now(UTC).timestamp()) > ttl:
                return False
            return bool(item.get("drupal_pending", False))
        except ClientError as e:
            logger.error(f"Error reading Drupal pending marker for {email}: {e}")
            return False

    @ensure_valid_credentials(role_name=role_name, region_name=dynamodb_region)
    def delete_drupal_pending(self, email: str) -> bool:
        """
        Delete the pending Drupal attribution marker for the given email.

        Called after the marker has been promoted to a durable user flag so the
        temporary record is cleaned up immediately rather than waiting for TTL.

        Args:
            email (str): Normalised (lowercased) email address

        Returns:
            bool: True if the deletion succeeded, False otherwise
        """
        try:
            table = self.dynamodb.Table(VALIDATION_CODE_TABLE_NAME)
            table.delete_item(Key={"email": f"{_DRUPAL_PENDING_PREFIX}{email}"})
            return True
        except ClientError as e:
            logger.error(f"Error deleting Drupal pending marker for {email}: {e}")
            return False
