import logging
from typing import Optional

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


class HubSpotService:
    BASE_URL = "https://api.hubapi.com"

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.HUBSPOT_TOKEN

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="HubSpot token is not configured",
            )
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def find_contact_by_email(self, email: str) -> Optional[str]:
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": email,
                        }
                    ]
                }
            ],
            "properties": ["email", "hs_marketable_status"],
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/crm/v3/objects/contacts/search",
                headers=self._headers(),
                json=payload,
            )

        if response.status_code >= 400:
            logger.error(
                "HubSpot contact lookup failed for email=%s status=%s body=%s",
                email,
                response.status_code,
                response.text,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to search HubSpot contact",
            )

        data = response.json()
        results = data.get("results") or []
        if not results:
            return None

        return results[0].get("id")

    async def update_marketable_status(self, contact_id: str, enabled: bool) -> None:
        payload = {
            "properties": {
                "hs_marketable_status": "true" if enabled else "false",
            }
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.patch(
                f"{self.BASE_URL}/crm/v3/objects/contacts/{contact_id}",
                headers=self._headers(),
                json=payload,
            )

        if response.status_code >= 400:
            logger.error(
                "HubSpot contact update failed contact_id=%s status=%s body=%s",
                contact_id,
                response.status_code,
                response.text,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to update HubSpot contact",
            )
