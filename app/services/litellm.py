import requests
from fastapi import HTTPException, status
import logging
from app.core.resource_limits import DEFAULT_KEY_DURATION, DEFAULT_MAX_SPEND, DEFAULT_RPM_PER_KEY
from app.core.config import settings
from typing import Optional

logger = logging.getLogger(__name__)

class LiteLLMService:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.master_key = api_key

        if not self.api_url:
            raise ValueError("LiteLLM API URL is required")
        if not self.master_key:
            raise ValueError("LiteLLM API key is required")

    @staticmethod
    def format_team_id(region_name: str, team_id: int) -> str:
        """Generate the correctly formatted team_id for LiteLLM"""
        return f"{region_name.replace(' ', '_')}_{team_id}"

    async def create_key(self, email: str, name: str, user_id: int, team_id: str, duration: str = f"{DEFAULT_KEY_DURATION}d", max_budget: float = DEFAULT_MAX_SPEND, rpm_limit: int = DEFAULT_RPM_PER_KEY) -> str:
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

            if settings.ENABLE_LIMITS:
                request_data["duration"] = "365d" # Sets the expiry date for the key
                request_data["budget_duration"] = duration
                request_data["max_budget"] = max_budget
                request_data["rpm_limit"] = rpm_limit
            else:
                request_data["duration"] = "365d"

            if user_id is not None:
                request_data["user_id"] = str(user_id)

            logger.info(f"Making request to LiteLLM API to generate key with data: {request_data}")
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

            # Treat 404 (key not found) as success
            if response.status_code == 404:
                return True

            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            logger.error(f"Error deleting LiteLLM key: {error_msg}")
            raise HTTPException(
                status_code=e.response.status_code if hasattr(e, 'response') and e.response is not None else status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete LiteLLM key: {error_msg}"
            )

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
            logger.info(f"LiteLLM key information: {response.json()}")
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

    async def update_budget(self, litellm_token: str, budget_duration: str, budget_amount: Optional[float] = None):
        """Update the budget for a LiteLLM API key"""
        try:
            # Update budget period in LiteLLM
            request_data = {
                "key": litellm_token,
                "budget_duration": budget_duration,
                "duration": "365d"
            }
            if budget_amount:
                request_data["max_budget"] = budget_amount

            response = requests.post(
                f"{self.api_url}/key/update",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                json=request_data
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

    async def set_key_restrictions(self, litellm_token: str, duration: str, budget_amount: float, rpm_limit: int, budget_duration: Optional[str] = None):
        """Set the restrictions for a LiteLLM API key"""
        try:
            response = requests.post(
                f"{self.api_url}/key/update",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                json={
                    "key": litellm_token,
                    "duration": duration,
                    "budget_duration": budget_duration,
                    "max_budget": budget_amount,
                    "rpm_limit": rpm_limit
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
                detail=f"Failed to set LiteLLM key restrictions: {error_msg}"
            )

    async def update_key_team_association(self, litellm_token: str, new_team_id: str):
        """Update the team association for a LiteLLM API key"""
        try:
            response = requests.post(
                f"{self.api_url}/key/update",
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                },
                json={
                    "key": litellm_token,
                    "team_id": new_team_id
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
                detail=f"Failed to update LiteLLM key team association: {error_msg}"
            )
