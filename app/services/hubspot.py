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

    async def create_contact_with_marketable_status(
        self, email: str, enabled: bool
    ) -> None:
        """Create a HubSpot contact and set hs_marketable_status at creation time.

        If the contact already exists, HubSpot returns 409 and we treat it as a no-op.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "properties": {
                    "email": email,
                    "hs_marketable_status": "true" if enabled else "false",
                }
            }
            response = await client.post(
                f"{self.BASE_URL}/crm/v3/objects/contacts",
                headers=self._headers(),
                json=payload,
            )
            if response.status_code == 409:
                logger.info("HubSpot contact already exists for email=%s", email)
                return
            if response.status_code >= 400:
                request_id = response.headers.get("x-hubspot-request-id", "unknown")
                logger.error(
                    "HubSpot contact create failed status=%s request_id=%s body=%s",
                    response.status_code,
                    request_id,
                    response.text[:500],
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to create HubSpot contact",
                )
