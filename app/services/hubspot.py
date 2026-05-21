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

    async def _upsert_contacts_marketable_status(
        self, contacts: list[tuple[str, bool]]
    ) -> None:
        if not contacts:
            return
        payload = {
            "inputs": [
                {
                    "idProperty": "email",
                    "id": email,
                    "properties": {
                        "email": email,
                        "hs_marketable_status": "true" if enabled else "false",
                    },
                }
                for email, enabled in contacts
            ]
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.BASE_URL}/crm/v3/objects/contacts/batch/upsert",
                headers=self._headers(),
                json=payload,
            )

        if response.status_code >= 400:
            request_id = response.headers.get("x-hubspot-request-id", "unknown")
            body_excerpt = response.text[:500]
            logger.error(
                "HubSpot contact upsert failed status=%s request_id=%s body_excerpt=%s",
                response.status_code,
                request_id,
                body_excerpt,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to upsert HubSpot contact",
            )

    async def upsert_contact_marketable_status(self, email: str, enabled: bool) -> None:
        await self._upsert_contacts_marketable_status([(email, enabled)])

    async def upsert_contacts_marketable_status(
        self, contacts: list[tuple[str, bool]]
    ) -> None:
        await self._upsert_contacts_marketable_status(contacts)
