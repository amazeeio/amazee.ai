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

    async def _get_contact_vid(
        self, email: str, client: httpx.AsyncClient
    ) -> Optional[int]:
        """Look up a HubSpot contact's vid (numeric ID) by email address."""
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": email}
                    ]
                }
            ],
            "properties": ["email"],
        }
        response = await client.post(
            f"{self.BASE_URL}/crm/v3/objects/contacts/search",
            headers=self._headers(),
            json=payload,
        )
        if response.status_code >= 400:
            logger.warning(
                "HubSpot contact search failed for email=%s status=%s",
                email,
                response.status_code,
            )
            return None
        results = response.json().get("results", [])
        if not results:
            logger.warning("HubSpot contact not found for email=%s", email)
            return None
        return int(results[0]["id"])

    async def _set_marketing_contacts_status(
        self, contacts: list[tuple[str, bool]]
    ) -> None:
        """Update marketing contact status using the Marketing Contacts API.

        hs_marketable_status is a read-only computed property — it cannot be set
        via the CRM Contacts API.  The Marketing Contacts API is the only way to
        opt contacts in or out of marketing communications.
        """
        if not contacts:
            return

        async with httpx.AsyncClient(timeout=10.0) as client:
            add_vids: list[dict] = []
            remove_vids: list[dict] = []

            for email, enabled in contacts:
                vid = await self._get_contact_vid(email, client)
                if vid is None:
                    continue
                if enabled:
                    add_vids.append({"vid": vid})
                else:
                    remove_vids.append({"vid": vid})

            if add_vids:
                response = await client.post(
                    f"{self.BASE_URL}/marketingcontacts/v1/contacts",
                    headers=self._headers(),
                    json={"vidsByLastUpdated": add_vids},
                )
                if response.status_code >= 400:
                    request_id = response.headers.get("x-hubspot-request-id", "unknown")
                    logger.error(
                        "HubSpot marketing contacts add failed status=%s request_id=%s body=%s",
                        response.status_code,
                        request_id,
                        response.text[:500],
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to add HubSpot marketing contacts",
                    )

            if remove_vids:
                response = await client.post(
                    f"{self.BASE_URL}/marketingcontacts/v1/contacts/removals",
                    headers=self._headers(),
                    json={"vidsByLastUpdated": remove_vids},
                )
                if response.status_code >= 400:
                    request_id = response.headers.get("x-hubspot-request-id", "unknown")
                    logger.error(
                        "HubSpot marketing contacts remove failed status=%s request_id=%s body=%s",
                        response.status_code,
                        request_id,
                        response.text[:500],
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Failed to remove HubSpot marketing contacts",
                    )

    async def upsert_contact_marketable_status(self, email: str, enabled: bool) -> None:
        await self._set_marketing_contacts_status([(email, enabled)])

    async def upsert_contacts_marketable_status(
        self, contacts: list[tuple[str, bool]]
    ) -> None:
        await self._set_marketing_contacts_status(contacts)
