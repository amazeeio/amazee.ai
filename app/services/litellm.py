import httpx
from fastapi import HTTPException, status
import logging
import re
from app.core.limit_service import (
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
)
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

    @staticmethod
    def sanitize_alias(alias: str) -> str:
        """
        Sanitize key_alias to follow LiteLLM rules:
        - Must be 2-255 chars
        - Start and end with alphanumeric character
        - Only allow a-zA-Z0-9_-/.
        - Replace @ with _at_
        """
        if not alias:
            return ""

        # Replace @ with _at_
        sanitized = alias.replace("@", "_at_")

        # Replace spaces with _
        sanitized = sanitized.replace(" ", "_")

        # Only allow a-zA-Z0-9_-/.
        # Replace anything else with _
        sanitized = re.sub(r"[^a-zA-Z0-9_\-\./]", "_", sanitized)

        # Collapse multiple underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        # Ensure it starts and ends with alphanumeric:
        # strip common non-alphanumeric boundary characters
        sanitized = sanitized.strip("_-. /")

        # Rule: 2-255 characters.
        # If it's too short after stripping, return empty so the caller can use a fallback.
        if len(sanitized) < 2:
            return ""

        # Enforce maximum length, but ensure we still end with an alphanumeric
        sanitized = sanitized[:255]
        sanitized = sanitized.rstrip("_-. /")

        # Re-check minimum length after enforcing trailing-character rule
        if len(sanitized) < 2:
            return ""

        return sanitized

    async def create_key(
        self,
        email: str,
        name: str,
        user_id: int,
        team_id: str,
        duration: Optional[str] = f"{DEFAULT_KEY_DURATION}d",
        max_budget: Optional[float] = DEFAULT_MAX_SPEND,
        rpm_limit: Optional[int] = DEFAULT_RPM_PER_KEY,
        apply_limits: bool = True,
    ) -> str:
        """Create a new API key for LiteLLM"""
        try:
            logger.info(
                f"Creating new LiteLLM API key for email: {email}, name: {name}, user_id: {user_id}, team_id: {team_id}"
            )
            request_data = {
                "models": ["all-team-models"],  # Allow access to all models
                "aliases": {},
                "config": {},
                "spend": 0,
            }

            # If name is empty or otherwise falsy, generate a default based on user_id
            actual_name = name if name else f"key-{user_id or 'unknown'}"

            # Add email and name to key_alias and metadata if provided
            # LiteLLM now requires key_alias to be set
            # Use "email - name" format for key_alias as requested
            clean_alias = self.sanitize_alias(f"{email or 'unknown'} - {actual_name}")

            if not clean_alias:
                # If still empty, use a safe default that's guaranteed to be valid
                clean_alias = f"key-{user_id or 'unknown'}"

            metadata = {"service_account_id": email or "unknown"}
            metadata["amazeeai_private_ai_key_name"] = actual_name

            # Add user_id to metadata if provided
            metadata["amazeeai_user_id"] = str(user_id or None)
            metadata["amazeeai_team_id"] = team_id

            request_data["key_alias"] = clean_alias
            request_data["metadata"] = metadata
            request_data["team_id"] = team_id

            request_data["duration"] = "365d"  # Sets the key expiry date
            if settings.ENABLE_LIMITS and apply_limits:
                if duration is None or max_budget is None or rpm_limit is None:
                    raise ValueError(
                        "duration, max_budget, and rpm_limit are required when apply_limits=True"
                    )
                # Per-key budget limits. Skipped for pool budget teams — the
                # team-level max_budget set by purchase_pool_budget is the
                # sole spending ceiling for those teams.
                request_data["budget_duration"] = duration
                request_data["max_budget"] = max_budget
                request_data["rpm_limit"] = rpm_limit

            if user_id is not None:
                request_data["user_id"] = str(user_id)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/generate",
                    json=request_data,
                    headers={"Authorization": f"Bearer {self.master_key}"},
                )

                response.raise_for_status()
                response_data = response.json()
                key = response_data["key"]
                logger.info("Successfully generated new LiteLLM API key")
                return key
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if hasattr(e, "response") and e.response is not None:
                # Preserve 4xx status codes from LiteLLM (client errors)
                if 400 <= e.response.status_code < 500:
                    status_code = e.response.status_code
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            logger.error(f"Error creating LiteLLM key: {error_msg}")
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to create LiteLLM key: {error_msg}",
            )

    async def delete_key(self, key: str) -> bool:
        """Delete a LiteLLM API key"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/delete",
                    json={"keys": [key]},  # API expects an array of keys
                    headers={"Authorization": f"Bearer {self.master_key}"},
                )

                # Treat 404 (key not found) as success
                if response.status_code == 404:
                    return True

                response.raise_for_status()
                return True
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            logger.error(f"Error deleting LiteLLM key: {error_msg}")
            raise HTTPException(
                status_code=e.response.status_code
                if hasattr(e, "response") and e.response is not None
                else status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete LiteLLM key: {error_msg}",
            )

    async def get_key_info(self, litellm_token: str) -> dict:
        """Get information about a LiteLLM API key"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/key/info",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    params={"key": litellm_token},
                )
                response.raise_for_status()
                response_data = response.json()
                logger.info("Successfully retrieved LiteLLM key information")
                return response_data
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            logger.error(f"Error getting LiteLLM key information: {error_msg}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get LiteLLM key information: {error_msg}",
            )

    async def update_budget(
        self,
        litellm_token: str,
        budget_duration: str,
        budget_amount: Optional[float] = None,
    ):
        """Update the budget for a LiteLLM API key"""
        try:
            # Update budget period in LiteLLM
            request_data = {
                "key": litellm_token,
                "budget_duration": budget_duration,
                "duration": "365d",
            }
            if budget_amount:
                request_data["max_budget"] = budget_amount

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM budget: {error_msg}",
            )

    async def update_key_duration(self, litellm_token: str, duration: str):
        """Update the duration for a LiteLLM API key"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={"key": litellm_token, "duration": duration},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM key duration: {error_msg}",
            )

    async def set_key_restrictions(
        self,
        litellm_token: str,
        duration: str,
        budget_amount: float,
        rpm_limit: int,
        budget_duration: Optional[str] = None,
    ):
        """Set the restrictions for a LiteLLM API key"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={
                        "key": litellm_token,
                        "duration": duration,
                        "budget_duration": budget_duration,
                        "max_budget": budget_amount,
                        "rpm_limit": rpm_limit,
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to set LiteLLM key restrictions: {error_msg}",
            )

    async def update_key_team_association(self, litellm_token: str, new_team_id: str):
        """Update the team association for a LiteLLM API key"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={"key": litellm_token, "team_id": new_team_id},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM key team association: {error_msg}",
            )

    async def get_team_info(self, team_id: str) -> dict:
        """Get information about a LiteLLM team including budget"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/team/info",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    params={"team_id": team_id},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get LiteLLM team info: {error_msg}",
            )

    async def create_team(
        self,
        max_budget: float = 0.0,
        budget_duration: Optional[str] = None,
        team_id: Optional[str] = None,
        team_alias: Optional[str] = None,
    ):
        """Create a LiteLLM team. Treat existing team as success."""
        try:
            request_data = {
                "max_budget": max_budget,
            }
            if team_id:
                request_data["team_id"] = team_id
            if team_alias:
                request_data["team_alias"] = team_alias
            if budget_duration:
                request_data["budget_duration"] = budget_duration

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/new",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
                identifier = team_id or team_alias or "unknown-team"
                logger.info(f"Created team {identifier} in LiteLLM")
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code
                response_text = e.response.text or ""
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {response_text}"

            # Some LiteLLM versions return 400/409 when team already exists.
            if status_code in (400, 409) and "already" in response_text.lower():
                identifier = team_id or team_alias or "unknown-team"
                logger.info(f"LiteLLM team {identifier} already exists; continuing")
                return

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create LiteLLM team: {error_msg}",
            )

    async def update_team_budget(
        self, team_id: str, max_budget: float, budget_duration: Optional[str] = None
    ):
        """Update the budget for a LiteLLM team"""
        try:
            request_data = {
                "team_id": team_id,
                "max_budget": max_budget,
            }
            if budget_duration:
                request_data["budget_duration"] = budget_duration

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
                logger.info(f"Updated team {team_id} budget to {max_budget} in LiteLLM")
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM team budget: {error_msg}",
            )
