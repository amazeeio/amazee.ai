import logging
from typing import Optional

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


class HubSpotService:
    BASE_URL = "https://api.hubapi.com"
    CONTACTS_OBJECT_PATH = "/crm/v3/objects/contacts"
    CONTACT_SEARCH_PATH = "/crm/v3/objects/contacts/search"
    EMAIL_SUBSCRIPTION_PATH = "/email/public/v1/subscriptions/{email}"

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.HUBSPOT_TOKEN
        self.marketing_updates_property = settings.HUBSPOT_MARKETING_UPDATES_PROPERTY
        self.marketing_subscription_id = settings.HUBSPOT_MARKETING_SUBSCRIPTION_ID

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

    async def _get_contact_id_by_email(
        self, email: str, client: httpx.AsyncClient
    ) -> Optional[str]:
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": email}
                    ]
                }
            ],
            "properties": ["email"],
            "limit": 1,
        }
        response = await client.post(
            f"{self.BASE_URL}{self.CONTACT_SEARCH_PATH}",
            headers=self._headers(),
            json=payload,
        )
        if response.status_code >= 400:
            request_id = response.headers.get("x-hubspot-request-id", "unknown")
            logger.error(
                "HubSpot contact search failed status=%s request_id=%s body=%s",
                response.status_code,
                request_id,
                response.text[:500],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to search HubSpot contact",
            )

        results = response.json().get("results", [])
        if not results:
            return None
        return results[0].get("id")

    async def _create_contact(self, email: str, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.BASE_URL}{self.CONTACTS_OBJECT_PATH}",
            headers=self._headers(),
            json={"properties": {"email": email}},
        )
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
        return str(response.json().get("id"))

    async def _update_contact_marketing_property(
        self, contact_id: str, enabled: bool, client: httpx.AsyncClient
    ) -> None:
        response = await client.patch(
            f"{self.BASE_URL}{self.CONTACTS_OBJECT_PATH}/{contact_id}",
            headers=self._headers(),
            json={
                "properties": {
                    self.marketing_updates_property: "true" if enabled else "false"
                }
            },
        )
        if response.status_code >= 400:
            request_id = response.headers.get("x-hubspot-request-id", "unknown")
            logger.error(
                "HubSpot contact property update failed status=%s request_id=%s body=%s",
                response.status_code,
                request_id,
                response.text[:500],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to update HubSpot contact marketing property",
            )

    async def _update_email_subscription(
        self, email: str, enabled: bool, client: httpx.AsyncClient
    ) -> None:
        if not self.marketing_subscription_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="HubSpot marketing subscription is not configured",
            )
        response = await client.put(
            f"{self.BASE_URL}{self.EMAIL_SUBSCRIPTION_PATH.format(email=email)}",
            headers=self._headers(),
            json={
                "subscriptionId": self.marketing_subscription_id,
                "subscribed": enabled,
            },
        )
        if response.status_code >= 400:
            request_id = response.headers.get("x-hubspot-request-id", "unknown")
            logger.error(
                "HubSpot subscription update failed status=%s request_id=%s body=%s",
                response.status_code,
                request_id,
                response.text[:500],
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to update HubSpot email subscription",
            )

    async def upsert_contact_marketing_updates(self, email: str, enabled: bool) -> None:
        """Upsert contact and sync marketing updates state.

        - If contact exists: update custom contact property + email subscription.
        - If contact does not exist: create contact first, then update both.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            contact_id = await self._get_contact_id_by_email(email=email, client=client)
            if not contact_id:
                contact_id = await self._create_contact(email=email, client=client)
            await self._update_contact_marketing_property(
                contact_id=contact_id, enabled=enabled, client=client
            )
            await self._update_email_subscription(
                email=email, enabled=enabled, client=client
            )
