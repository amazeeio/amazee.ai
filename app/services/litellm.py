import requests
from fastapi import HTTPException, status
import os
import logging

logger = logging.getLogger(__name__)

class LiteLLMService:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.master_key = api_key

        if not self.api_url:
            raise ValueError("LiteLLM API URL is required")
        if not self.master_key:
            raise ValueError("LiteLLM API key is required")

    async def create_key(self, email: str, name: str, user_id: int, team_id: str) -> str:
        """Create a new API key for LiteLLM"""
        try:
            logger.info(f"Creating new LiteLLM API key for email: {email}, name: {name}, user_id: {user_id}, team_id: {team_id}")
            request_data = {
                "models": ["all-team-models"],   # Allow access to all models
                "aliases": {},
                "config": {},
                "spend": 0,
            }

            # Add email and name to key_alias and metadata if provided
            key_alias = f"{email} - {name}"
            metadata = {"service_account_id": email}
            metadata["amazeeai_private_ai_key_name"] = name

            # Add user_id to metadata if provided
            metadata["amazeeai_user_id"] = str(user_id or None)
            metadata["amazeeai_team_id"] = team_id

            request_data["key_alias"] = key_alias
            request_data["metadata"] = metadata
            request_data["team_id"] = team_id

            if os.getenv("EXPIRE_KEYS", "").lower() == "true":
                request_data["duration"] = "30d"
                request_data["budget_duration"] = "30d"
                request_data["max_budget"] = 20.0
                request_data["rpm_limit"] = 500
            else:
                request_data["duration"] = "365d"

            if user_id is not None:
                request_data["user_id"] = str(user_id)

            logger.info("Making request to LiteLLM API to generate key")
            response = requests.post(
                f"{self.api_url}/key/generate",
                json=request_data,
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                }
            )

            response.raise_for_status()
            key = response.json()["key"]
            logger.info("Successfully generated new LiteLLM API key")
            return key
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            logger.error(f"Error creating LiteLLM key: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create LiteLLM key: {error_msg}"
            )

    async def delete_key(self, key: str) -> bool:
        """Delete a LiteLLM API key"""
        try:
            response = requests.post(
                f"{self.api_url}/key/delete",
                json={"keys": [key]},  # API expects an array of keys
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                }
            )

            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error deleting LiteLLM key: {str(e)}")
            return False

    async def get_key_info(self, litellm_token: str) -> dict:
        """Get information about a LiteLLM API key"""
        try:
            response = requests.get(
                f"{self.api_url}/key/info",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                params={
                    "key": litellm_token
                }
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            logger.error(f"Error getting LiteLLM key information: {error_msg}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get LiteLLM key information: {error_msg}"
            )

    async def update_budget(self, litellm_token: str, budget_duration: str):
        """Update the budget for a LiteLLM API key"""
        try:
            # Update budget period in LiteLLM
            response = requests.post(
                f"{self.api_url}/key/update",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                json={
                    "key": litellm_token,
                    "budget_duration": budget_duration
                }
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM budget: {error_msg}"
            )

    async def update_key_duration(self, litellm_token: str, duration: str):
        """Update the duration for a LiteLLM API key"""
        try:
            response = requests.post(
                f"{self.api_url}/key/update",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                json={
                    "key": litellm_token,
                    "duration": duration
                }
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM key duration: {error_msg}"
            )
