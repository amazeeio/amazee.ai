import requests
from fastapi import HTTPException, status
import os

class LiteLLMService:
    def __init__(self, api_url: str = None, api_key: str = None):
        self.api_url = api_url or os.getenv("LITELLM_API_URL", "https://demo.litellm.ai")
        self.master_key = api_key or os.getenv("LITELLM_MASTER_KEY")
        if not self.master_key:
            raise ValueError("LiteLLM API key is required")

    async def create_key(self, email: str = None) -> str:
        """Create a new API key for LiteLLM"""
        try:
            request_data = {
                "duration": "8760h",  # Set token duration to 1 year (365 days * 24 hours)
                "models": ["*"],   # Allow access to all models
                "aliases": {},
                "config": {},
                "spend": 0,
            }
            
            # Add email as key_alias and metadata if provided
            if email:
                request_data["key_alias"] = email
                request_data["metadata"] = {"service_account_id": email}
            
            response = requests.post(
                f"{self.api_url}/key/generate",
                json=request_data,
                headers={
                    "Authorization": f"Bearer {self.master_key}"
                }
            )

            response.raise_for_status()
            return response.json()["key"]
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            print(f"Error creating LiteLLM key: {error_msg}")
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
            print(f"Error deleting LiteLLM key: {str(e)}")
            return False