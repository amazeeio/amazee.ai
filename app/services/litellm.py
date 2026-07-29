import asyncio
import hashlib
import httpx
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException, status
import logging
import os
import re
from app.core.limit_service import (
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
)
from app.core.config import settings
from typing import Optional

logger = logging.getLogger(__name__)

# Concurrent /key/list page fetches per region sweep. Pages are independent
# read-only requests, so this only bounds load on the LiteLLM proxy.
LIST_KEYS_PAGE_CONCURRENCY = max(1, int(os.getenv("LIST_KEYS_PAGE_CONCURRENCY", "8")))

# LiteLLM treats a key's team_id as team membership, so any team key can read
# the whole team via management routes (/team/info returns every sibling key's
# owner, spend and budget). `llm_api_routes` is LiteLLM's route
# group for inference only — /chat/completions, /embeddings, /responses,
# /v1/messages, /models, pass-through providers — and excludes /team/*, /key/*,
# /user/* and /spend/*. Used for keys that must stay private from their own
# team, i.e. the shared anonymous-trial team.
#
# /model/info is added explicitly: it is an info route, NOT part of
# `llm_api_routes`. The Drupal module (`ai_provider_amazeeio`) lists models
# through it with whatever key it holds — trial keys and self-service keys
# alike — from `AmazeeClient::models()` and from the provider config form, which
# needs the human-readable description that the OpenAI-style /models list does
# not carry. Unrestricted keys reach it anyway, so only the route-restricted
# trial keys need it named here; without it those sites 403 on model discovery.
# It exposes the region's model catalogue only, not other keys.
INFERENCE_ONLY_ROUTES = ["llm_api_routes", "/model/info"]

# Timeout for regional LiteLLM model calls, which may run inside a BackgroundTask
# with no retry — a hung proxy must not park the task on 'pending' forever.
MODEL_HTTP_TIMEOUT = 30.0


def hash_litellm_token(litellm_token: str) -> str:
    """Hash a LiteLLM key the way LiteLLM stores it internally.

    LiteLLM persists ``sk-`` keys as their SHA-256 hexdigest (e.g. in the
    ``LiteLLM_SpendLogs`` table queried by ``/spend/logs/v2`` and the
    daily-spend tables that back ``/user/daily/activity``). Any other token
    is stored verbatim. Mirrors LiteLLM's ``_hash_token_if_needed``.

    Exposed as a module-level function, not only as
    :meth:`LiteLLMService.hash_token`, so callers can hash without holding a
    service instance and without depending on the class symbol - which tests
    routinely patch, silently breaking hash-keyed lookups.
    """
    if litellm_token.startswith("sk-"):
        return hashlib.sha256(litellm_token.encode()).hexdigest()
    return litellm_token


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
    def hash_token(litellm_token: str) -> str:
        """Hash a LiteLLM key the way LiteLLM stores it internally.

        Thin wrapper over :func:`hash_litellm_token`, kept for existing callers.
        """
        return hash_litellm_token(litellm_token)

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

    @staticmethod
    def _parse_http_error(e: httpx.HTTPStatusError) -> tuple[int, str, str]:
        status_code = (
            e.response.status_code
            if hasattr(e, "response") and e.response is not None
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        response_text = ""
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            response_text = e.response.text or ""
            try:
                error_msg = f"Status {e.response.status_code}: {e.response.json()}"
            except ValueError:
                error_msg = f"Status {e.response.status_code}: {response_text}"
        return status_code, error_msg, response_text

    @staticmethod
    def _is_idempotent_litellm_error(
        status_code: int, response_text: str, allowed_markers: list[str]
    ) -> bool:
        if status_code not in (400, 404, 409):
            return False
        lowered = (response_text or "").lower()
        return any(marker in lowered for marker in allowed_markers)

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
        blocked: Optional[bool] = None,
        allowed_routes: Optional[list[str]] = None,
    ) -> str:
        """Create a new API key for LiteLLM

        Args:
            allowed_routes: Restrict the key to these LiteLLM routes (exact
                paths, wildcards or route-group names such as
                ``llm_api_routes``). None means no route restriction.
        """
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
            if blocked is not None:
                request_data["blocked"] = blocked
            if allowed_routes:
                request_data["allowed_routes"] = allowed_routes

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
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to get LiteLLM key information: {error_msg}",
            )

    async def _fetch_key_page(
        self, client: httpx.AsyncClient, page: int, page_size: int
    ) -> dict:
        """Fetch a single ``/key/list`` page and return the decoded payload."""
        response = await client.get(
            f"{self.api_url}/key/list",
            headers={"Authorization": f"Bearer {self.master_key}"},
            params={
                "page": page,
                "size": page_size,
                "return_full_object": True,
                "include_team_keys": True,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _collect_key_page(payload: dict, keys: dict[str, dict]) -> list:
        """Merge one page's keys into ``keys``. Returns the raw batch."""
        batch = payload.get("keys") or []
        for entry in batch:
            # With return_full_object=true entries are objects; be defensive in
            # case LiteLLM returns bare token strings.
            if not isinstance(entry, dict):
                continue
            token = entry.get("token")
            if token:
                keys[token] = entry
        return batch

    async def list_all_keys(self, page_size: int = 100) -> dict[str, dict]:
        """Return every key in this region, keyed by LiteLLM's hashed token.

        Bulk replacement for calling :meth:`get_key_info` once per key. The
        reconciliation job needs the state of every key in a region, which as a
        per-key loop costs one HTTP round-trip each (~0.18s), i.e. ~40 minutes
        at 13.5k keys. ``/key/list`` returns the same full key objects 100 at a
        time, so the same sweep costs ``ceil(n / 100)`` requests instead of n.

        ``page_size`` is capped at 100 by LiteLLM (values above that are
        rejected with a 422), so it is clamped rather than passed through.

        The returned mapping is keyed the way LiteLLM stores tokens - the
        SHA-256 hexdigest of an ``sk-`` key - so look values up via
        :meth:`hash_token`. Values are the flat key objects, matching the
        ``info`` sub-dict that :meth:`get_key_info` returns.
        """
        page_size = max(1, min(int(page_size), 100))
        keys: dict[str, dict] = {}
        try:
            async with httpx.AsyncClient() as client:
                first = await self._fetch_key_page(client, 1, page_size)
                batch = self._collect_key_page(first, keys)
                pages_fetched = 1

                # A short or empty first page is the only page.
                if batch and len(batch) >= page_size:
                    total_pages = first.get("total_pages") or 0
                    if total_pages and total_pages > 1:
                        # Page count known: fetch the rest concurrently. Pages
                        # are independent reads, so this turns a 119-page walk
                        # from minutes into seconds.
                        semaphore = asyncio.Semaphore(LIST_KEYS_PAGE_CONCURRENCY)

                        async def _page(page_number: int) -> dict:
                            async with semaphore:
                                return await self._fetch_key_page(
                                    client, page_number, page_size
                                )

                        payloads = await asyncio.gather(
                            *[_page(p) for p in range(2, total_pages + 1)]
                        )
                        for payload in payloads:
                            self._collect_key_page(payload, keys)
                        pages_fetched = total_pages
                    else:
                        # total_pages absent: fall back to a sequential walk,
                        # since we cannot know how many pages to request.
                        page = 2
                        while True:
                            payload = await self._fetch_key_page(
                                client, page, page_size
                            )
                            batch = self._collect_key_page(payload, keys)
                            pages_fetched = page
                            if not batch or len(batch) < page_size:
                                break
                            page += 1

            logger.info(
                "Listed %d LiteLLM keys from %s across %d page(s)",
                len(keys),
                self.api_url,
                pages_fetched,
            )
            return keys
        except httpx.HTTPStatusError as e:
            status_code, error_msg, _ = self._parse_http_error(e)
            logger.error("Error listing LiteLLM keys: %s", error_msg)
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to list LiteLLM keys: {error_msg}",
            )

    async def get_key_last_used(self, litellm_token: str) -> Optional[datetime]:
        """Return the timestamp a key was last used, or ``None`` if never used.

        Derives the last-used time from LiteLLM's spend logs: the most recent
        ``startTime`` recorded for the key. Uses the paginated
        ``/spend/logs/v2`` endpoint sorted by ``startTime`` descending with
        ``page_size=1``, so only the single latest row is transferred
        regardless of how many requests the key has made.
        """
        hashed_token = self.hash_token(litellm_token)
        # /spend/logs/v2 requires an explicit range; use an all-encompassing
        # window so we capture the key's very first through most-recent usage.
        start_date = "1970-01-01 00:00:00"
        end_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/spend/logs/v2",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    params={
                        "api_key": hashed_token,
                        "start_date": start_date,
                        "end_date": end_date,
                        "page": 1,
                        "page_size": 1,
                        "sort_by": "startTime",
                        "sort_order": "desc",
                    },
                )
                response.raise_for_status()
                data = response.json()
            rows = data.get("data") or []
            if not rows:
                return None
            # Sorted startTime desc, so the first row holds max(startTime).
            start_time = rows[0].get("startTime")
            if not start_time:
                return None
            return datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            logger.error(f"Error getting LiteLLM key last-used time: {error_msg}")
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to get LiteLLM key last-used time: {error_msg}",
            )

    async def _fetch_daily_activity(
        self,
        endpoint: str,
        filter_params: dict,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
    ) -> list[dict]:
        """Paginate a LiteLLM daily-activity endpoint and return all rows.

        ``endpoint`` is the path to call (e.g. ``/user/daily/activity`` or
        ``/team/daily/activity``). ``filter_params`` carries the entity filter
        for that endpoint (e.g. ``{"api_key": ...}``, ``{"user_id": ...}`` or
        ``{"team_ids": ...}``). Paginates through every page and returns the raw
        ``results`` rows, each containing ``date``, ``metrics`` and
        ``breakdown``.

        These values come from LiteLLM's pre-aggregated daily-spend tables,
        which are keyed on whole UTC days and are independent of our
        billing-cycle spend resets — so they are a true continuous usage history
        and will NOT reconcile with the cycle-reset spend/budget figures
        returned by the other spend endpoints. The current UTC day may
        under-report until LiteLLM's next batch flush.
        """
        results: list[dict] = []
        page = 1
        max_pages = 100
        try:
            async with httpx.AsyncClient() as client:
                while page <= max_pages:
                    response = await client.get(
                        f"{self.api_url}{endpoint}",
                        headers={"Authorization": f"Bearer {self.master_key}"},
                        params={
                            "start_date": start_date,
                            "end_date": end_date,
                            "page": page,
                            "page_size": page_size,
                            **filter_params,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    results.extend(data.get("results", []))
                    metadata = data.get("metadata") or {}
                    if not metadata.get("has_more"):
                        break
                    page += 1
            logger.info("Successfully retrieved LiteLLM daily activity")
            return results
        except httpx.HTTPStatusError as e:
            error_msg = str(e)
            logger.error(f"Error getting LiteLLM daily activity: {error_msg}")
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code
                try:
                    error_details = e.response.json()
                    error_msg = f"Status {e.response.status_code}: {error_details}"
                except ValueError:
                    error_msg = f"Status {e.response.status_code}: {e.response.text}"
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to get LiteLLM daily activity: {error_msg}",
            )

    async def get_daily_activity(
        self,
        litellm_token: str,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
    ) -> list[dict]:
        """Fetch per-day usage for a single key from LiteLLM.

        Proxies LiteLLM's ``/user/daily/activity`` endpoint, filtered to the
        given key (via its hashed token).
        """
        hashed_token = self.hash_token(litellm_token)
        return await self._fetch_daily_activity(
            "/user/daily/activity",
            {"api_key": hashed_token},
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
        )

    async def get_user_daily_activity(
        self,
        user_id: str,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
    ) -> list[dict]:
        """Fetch per-day usage for a single user from LiteLLM.

        Proxies LiteLLM's ``/user/daily/activity`` endpoint, filtered to the
        given user (aggregated across all of the user's keys).
        """
        return await self._fetch_daily_activity(
            "/user/daily/activity",
            {"user_id": user_id},
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
        )

    async def get_team_daily_activity(
        self,
        team_id: str,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
    ) -> list[dict]:
        """Fetch per-day usage for a single team from LiteLLM.

        Proxies LiteLLM's ``/team/daily/activity`` endpoint, filtered to the
        given LiteLLM ``team_id`` (aggregated across all of the team's keys).
        """
        return await self._fetch_daily_activity(
            "/team/daily/activity",
            {"team_ids": team_id},
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
        )

    async def get_all_team_daily_activity(
        self,
        start_date: str,
        end_date: str,
        page_size: int = 1000,
    ) -> list[dict]:
        """Fetch per-day usage for **every active team** in this region.

        Same endpoint as :meth:`get_team_daily_activity` but with no entity
        filter, which LiteLLM answers with one row per UTC day. Each row's
        ``breakdown.entities`` is keyed by LiteLLM team id and each
        ``breakdown.api_keys`` by hashed token, so a single sweep yields period
        spend for every team *and* key that had any traffic.

        This is deliberately O(active entities), not O(total keys): idle keys
        produce no daily-spend rows at all. That is what makes a frequent poll
        affordable at 100k keys, where enumerating every key is not.
        """
        return await self._fetch_daily_activity(
            "/team/daily/activity",
            {},
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
        )

    async def list_keys_for_team(
        self, team_id: str, page_size: int = 100
    ) -> list[dict]:
        """Return the full key objects for one LiteLLM team.

        Used to confirm exact ``spend`` and ``max_budget`` for a team that a
        daily-activity sweep has already flagged as worth looking at. Scoping
        by team keeps this cheap; an unscoped ``/key/list`` walk would defeat
        the point of the sweep.

        ``page_size`` is capped at 100 by LiteLLM (larger values 422), so it is
        clamped rather than passed through.
        """
        page_size = max(1, min(int(page_size), 100))
        keys: list[dict] = []
        page = 1
        max_pages = 1000
        try:
            async with httpx.AsyncClient() as client:
                while page <= max_pages:
                    response = await client.get(
                        f"{self.api_url}/key/list",
                        headers={"Authorization": f"Bearer {self.master_key}"},
                        params={
                            "team_id": team_id,
                            "page": page,
                            "size": page_size,
                            "return_full_object": "true",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    batch = [k for k in (data.get("keys") or []) if isinstance(k, dict)]
                    keys.extend(batch)
                    # Stop on a short or empty page. total_pages is only a
                    # secondary check: when it is absent, trusting it alone
                    # truncates the walk after page 1.
                    if len(batch) < page_size:
                        break
                    total_pages = data.get("total_pages") or 0
                    if total_pages and page >= total_pages:
                        break
                    page += 1
            return keys
        except httpx.HTTPStatusError as e:
            status_code, error_msg, _ = self._parse_http_error(e)
            logger.error(
                "Error listing LiteLLM keys for team %s: %s", team_id, error_msg
            )
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to list LiteLLM keys for team: {error_msg}",
            )

    async def update_budget(
        self,
        litellm_token: str,
        budget_duration: str,
        budget_amount: Optional[float] = None,
        include_max_budget: bool = False,
    ):
        """Update the budget for a LiteLLM API key"""
        try:
            # Update budget period in LiteLLM
            request_data = {
                "key": litellm_token,
                "budget_duration": budget_duration,
                "duration": "365d",
            }
            if include_max_budget or budget_amount is not None:
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

    async def update_key_budget(
        self,
        litellm_token: str,
        budget_duration: Optional[str] = None,
        max_budget: Optional[float] = None,
        clear_max_budget: bool = False,
        clear_budget_duration: bool = False,
        blocked: Optional[bool] = None,
    ) -> None:
        """Update budget fields for a LiteLLM key.

        When clear_max_budget=True, max_budget is explicitly sent as null.
        When clear_budget_duration=True, budget_duration is explicitly sent as null.
        This method intentionally avoids updating key duration/expiry.
        """
        try:
            request_data = {
                "key": litellm_token,
            }
            if clear_budget_duration or budget_duration is not None:
                request_data["budget_duration"] = budget_duration
            if clear_max_budget or max_budget is not None:
                request_data["max_budget"] = max_budget
            if blocked is not None:
                request_data["blocked"] = blocked

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
                detail=f"Failed to update LiteLLM key budget: {error_msg}",
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
        spend: Optional[float] = None,
        blocked: Optional[bool] = None,
    ):
        """Set the restrictions for a LiteLLM API key.

        Args:
            spend: When provided, overrides the key's spend counter
                   (e.g. 0.0 to reset spend at billing cycle start).
        """
        try:
            request_data = {
                "key": litellm_token,
                "duration": duration,
                "budget_duration": budget_duration,
                "max_budget": budget_amount,
                "rpm_limit": rpm_limit,
            }
            if spend is not None:
                request_data["spend"] = spend
            if blocked is not None:
                request_data["blocked"] = blocked
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
                detail=f"Failed to set LiteLLM key restrictions: {error_msg}",
            )

    async def set_key_allowed_routes(
        self, litellm_token: str, allowed_routes: list[str]
    ) -> None:
        """Scope an existing key to *allowed_routes* (used by the backfill)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/key/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={"key": litellm_token, "allowed_routes": allowed_routes},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to set LiteLLM key allowed_routes: {error_msg}",
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

    async def get_model_info(self) -> dict:
        """Get LiteLLM model info for this region."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/model/info",
                    headers={"Authorization": f"Bearer {self.master_key}"},
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
                detail=f"Failed to get LiteLLM model info: {error_msg}",
            )

    async def get_router_settings(self) -> dict:
        """Get LiteLLM router settings (includes model_group_alias)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/router/settings",
                    headers={"Authorization": f"Bearer {self.master_key}"},
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
                detail=f"Failed to get LiteLLM router settings: {error_msg}",
            )

    async def get_cost_margin_config(self) -> dict:
        """Get LiteLLM provider margin configuration."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/config/cost_margin_config",
                    headers={"Authorization": f"Bearer {self.master_key}"},
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
                detail=f"Failed to get LiteLLM margin config: {error_msg}",
            )

    async def get_user_info(self, user_id: str) -> dict:
        """Get information about a LiteLLM user including spend and keys."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/user/info",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    params={"user_id": user_id},
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
                detail=f"Failed to get LiteLLM user info: {error_msg}",
            )

    async def create_team(
        self,
        max_budget: Optional[float] = None,
        budget_duration: Optional[str] = None,
        team_id: Optional[str] = None,
        team_alias: Optional[str] = None,
        models: Optional[list[str]] = None,
    ):
        """Create a LiteLLM team. Treat existing team as success.

        Args:
            max_budget: Budget limit. None means no team-level budget gate.
                        0.0 blocks all requests (used for POOL teams).
            models: Access-group slugs the team may use (LiteLLM's `models`
                    field accepts access-group names). None = no restriction
                    (all proxy models).
        """
        try:
            request_data = {}
            if max_budget is not None:
                request_data["max_budget"] = max_budget
            if team_id:
                request_data["team_id"] = team_id
            if team_alias:
                request_data["team_alias"] = team_alias
            if budget_duration:
                request_data["budget_duration"] = budget_duration
            if models is not None:
                request_data["models"] = models

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
            status_code, error_msg, response_text = self._parse_http_error(e)

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
        self,
        team_id: str,
        max_budget: Optional[float],
        budget_duration: Optional[str] = None,
        spend: Optional[float] = None,
        model_aliases: Optional[dict[str, str]] = None,
        clear_budget_duration: bool = False,
    ):
        """Update the budget for a LiteLLM team.

        Args:
            max_budget: Budget limit. None removes the team-level budget gate.
                        0.0 blocks all requests. Positive float sets explicit limit.
            spend: When provided, overrides the team's spend counter
                   (e.g. 0.0 to reset spend at billing cycle start).
        """
        try:
            request_data = {
                "team_id": team_id,
            }
            # Always include max_budget, even when None, so LiteLLM receives
            # JSON null when the intent is to clear the team-level budget gate.
            request_data["max_budget"] = max_budget
            if clear_budget_duration or budget_duration is not None:
                request_data["budget_duration"] = budget_duration
            if spend is not None:
                request_data["spend"] = spend
            if model_aliases is not None:
                request_data["model_aliases"] = model_aliases

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
                logger.info(f"Updated team {team_id} budget to {max_budget} in LiteLLM")
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM team budget: {error_msg}",
            )

    async def update_team_models(self, team_id: str, models: list[str]) -> None:
        """Set a LiteLLM team's `models` list (access-group slugs).

        An empty list clears the restriction (LiteLLM treats [] as
        all-proxy-models) — used when a region's enforcement is turned off.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={"team_id": team_id, "models": models},
                )
                response.raise_for_status()
                logger.info(f"Updated team {team_id} models to {models} in LiteLLM")
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM team models: {error_msg}",
            )

    async def get_team_model_aliases(self, team_id: str) -> dict[str, str]:
        """Get a team's model_aliases map from LiteLLM."""
        raw_aliases = await self._fetch_team_model_aliases(team_id)
        if not isinstance(raw_aliases, dict):
            return {}
        return {
            str(alias): str(model)
            for alias, model in raw_aliases.items()
            if alias is not None and model is not None
        }

    async def _fetch_team_model_aliases(self, team_id: str) -> dict | None:
        team_info_response = await self.get_team_info(team_id)
        team_info = team_info_response.get("team_info", team_info_response)

        raw_aliases = team_info.get("model_aliases")
        if isinstance(raw_aliases, dict):
            return raw_aliases

        model_table = team_info.get("litellm_model_table")
        if model_table and isinstance(model_table, dict):
            raw_aliases = model_table.get("model_aliases")
            if isinstance(raw_aliases, dict):
                return raw_aliases

        return await self._fetch_team_model_aliases_from_list(team_id)

    async def _fetch_team_model_aliases_from_list(self, team_id: str) -> dict | None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/team/list",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                )
                response.raise_for_status()
                teams = response.json()
                for team in teams:
                    if team.get("team_id") == team_id:
                        model_table = team.get("litellm_model_table")
                        if model_table and isinstance(model_table, dict):
                            return model_table.get("model_aliases")
                return None
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to list LiteLLM teams: {error_msg}",
            )

    async def create_user(
        self,
        user_id: str,
        user_email: str,
        teams: Optional[list[str]] = None,
        auto_create_key: bool = False,
    ) -> None:
        """Create a LiteLLM user. Treat existing users as success."""
        request_data: dict = {
            "user_id": user_id,
            "user_email": user_email,
            "auto_create_key": auto_create_key,
        }
        if teams:
            request_data["teams"] = teams

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/user/new",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, response_text = self._parse_http_error(e)
            if self._is_idempotent_litellm_error(
                status_code, response_text, ["already exists", "duplicate"]
            ):
                logger.info(f"LiteLLM user {user_id} already exists; continuing")
                return
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create LiteLLM user: {error_msg}",
            )

    async def update_user(self, user_id: str, updates: dict) -> None:
        """Update a LiteLLM user."""
        request_data = {"user_id": user_id, **updates}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/user/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=request_data,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM user: {error_msg}",
            )

    async def delete_user(self, user_id: str) -> None:
        """Delete a LiteLLM user. Treat already-deleted users as success."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/user/delete",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={"user_ids": [user_id]},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, response_text = self._parse_http_error(e)
            if self._is_idempotent_litellm_error(
                status_code, response_text, ["not found", "does not exist"]
            ):
                logger.info(f"LiteLLM user {user_id} already absent; continuing")
                return
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete LiteLLM user: {error_msg}",
            )

    async def add_team_member(
        self, team_id: str, user_id: str, role: str = "user"
    ) -> None:
        """Add a user as a team member in LiteLLM. Treat existing membership as success."""
        payload = {
            "team_id": team_id,
            "member": {"user_id": user_id, "role": role},
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/member_add",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, response_text = self._parse_http_error(e)
            if self._is_idempotent_litellm_error(
                status_code, response_text, ["already", "exists"]
            ):
                logger.info(
                    f"LiteLLM team membership already exists team={team_id} user={user_id}; continuing"
                )
                return
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to add LiteLLM team member: {error_msg}",
            )

    async def update_team_member(
        self,
        team_id: str,
        user_id: str,
        role: str,
        max_budget_in_team: Optional[float] = None,
        budget_duration: Optional[str] = None,
    ) -> None:
        """Update a user's role/budget within a LiteLLM team.

        LiteLLM's /team/member_update ignores budget_duration (issue #25509).
        When budget_duration is provided, this method performs a two-step write:
        1. /team/member_update  -> sets max_budget_in_team
        2. /budget/update       -> sets budget_duration on the membership budget
        """
        payload = {"team_id": team_id, "user_id": user_id, "role": role}
        if max_budget_in_team is not None:
            payload["max_budget_in_team"] = max_budget_in_team
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/member_update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, response_text = self._parse_http_error(e)
            if self._is_idempotent_litellm_error(
                status_code,
                response_text,
                ["not found", "does not exist", "not a member", "already", "no change"],
            ):
                logger.info(
                    "LiteLLM member update noop team=%s user=%s; continuing",
                    team_id,
                    user_id,
                )
                if budget_duration is not None and max_budget_in_team is not None:
                    await self._update_membership_budget_duration(
                        team_id=team_id,
                        user_id=user_id,
                        max_budget=max_budget_in_team,
                        budget_duration=budget_duration,
                    )
                return
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM team member: {error_msg}",
            )

        if budget_duration is not None and max_budget_in_team is not None:
            await self._update_membership_budget_duration(
                team_id=team_id,
                user_id=user_id,
                max_budget=max_budget_in_team,
                budget_duration=budget_duration,
            )

    async def _update_membership_budget_duration(
        self,
        team_id: str,
        user_id: str,
        max_budget: float,
        budget_duration: str,
    ) -> None:
        """Set budget_duration on a team membership's budget table.

        Workaround for LiteLLM issue #25509 where /team/member_update
        ignores budget_duration. We look up the membership budget_id
        via /user/info, then POST it via /budget/update.
        """
        try:
            async with httpx.AsyncClient() as client:
                user_resp = await client.get(
                    f"{self.api_url}/user/info",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    params={"user_id": user_id},
                )
                user_resp.raise_for_status()
                user_data = user_resp.json()

            budget_id = None
            for team in user_data.get("teams", []):
                if team.get("team_id") != team_id:
                    continue
                for membership in team.get("team_memberships", []):
                    budget_table = membership.get("litellm_budget_table") or {}
                    budget_id = budget_table.get("budget_id") or membership.get(
                        "budget_id"
                    )
                    if budget_id:
                        break
                if budget_id:
                    break

            if not budget_id:
                logger.warning(
                    "No membership budget_id found for team=%s user=%s; "
                    "skipping budget_duration update",
                    team_id,
                    user_id,
                )
                return

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.api_url}/budget/update",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json={
                        "budget_id": budget_id,
                        "max_budget": max_budget,
                        "budget_duration": budget_duration,
                    },
                )
                resp.raise_for_status()
                logger.info(
                    "Updated membership budget_duration=%s for team=%s user=%s",
                    budget_duration,
                    team_id,
                    user_id,
                )
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            logger.error(
                "Failed to update membership budget_duration for team=%s user=%s: %s",
                team_id,
                user_id,
                error_msg,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update membership budget duration: {error_msg}",
            )

    async def remove_team_member(self, team_id: str, user_id: str) -> None:
        """Remove a user from a LiteLLM team. Treat missing membership as success."""
        payload = {"team_id": team_id, "user_id": user_id}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/team/member_delete",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, response_text = self._parse_http_error(e)
            if self._is_idempotent_litellm_error(
                status_code,
                response_text,
                ["not found", "does not exist", "not a member"],
            ):
                logger.info(
                    f"LiteLLM membership already absent team={team_id} user={user_id}; continuing"
                )
                return
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to remove LiteLLM team member: {error_msg}",
            )

    async def add_model(
        self,
        model_id: str,
        litellm_params: dict,
        access_groups: Optional[list[str]] = None,
    ) -> dict:
        """
        Register a new model in LiteLLM.
        Sends POST /model/new.
        """
        # Copy so we never mutate the caller's dict (it may back DBModel.litellm_params).
        payload = {
            "model_name": model_id,
            "litellm_params": dict(litellm_params or {}),
        }
        if access_groups is not None:
            payload["model_info"] = {"access_groups": access_groups}
        if "model" not in payload["litellm_params"]:
            payload["litellm_params"]["model"] = model_id

        try:
            async with httpx.AsyncClient(timeout=MODEL_HTTP_TIMEOUT) as client:
                response = await client.post(
                    f"{self.api_url}/model/new",
                    headers={"Authorization": f"Bearer {self.master_key}"},
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            status_code, error_msg, _ = self._parse_http_error(e)
            # Preserve 4xx (e.g. 409 already-exists) so callers can detect it.
            if not 400 <= status_code < 500:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            logger.error("Failed to add model %s to LiteLLM: %s", model_id, error_msg)
            raise HTTPException(
                status_code=status_code,
                detail=f"Failed to add LiteLLM model: {error_msg}",
            )

    async def get_model_deployment_ids(self, model_id: str) -> list[str]:
        """
        Resolve the LiteLLM deployment id(s) for a public model_name.
        /model/update and /model/delete key on model_info.id, not model_name,
        and /model/new allows duplicate model_names — so callers must resolve
        ids first to upsert/delete correctly.
        """
        info = await self.get_model_info()
        entries = info.get("data") or []
        if not isinstance(entries, list):
            return []
        return [
            entry["model_info"]["id"]
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("model_name") == model_id
            and isinstance(entry.get("model_info"), dict)
            and entry["model_info"].get("id")
        ]

    async def update_model(
        self,
        model_id: str,
        litellm_params: dict,
        deployment_ids: Optional[list[str]] = None,
        access_groups: Optional[list[str]] = None,
    ) -> dict:
        """
        Update an existing model in LiteLLM.
        Sends POST /model/update per deployment id (LiteLLM identifies the
        deployment by model_info.id; model_name alone is not accepted).
        access_groups=[] clears the tags; None leaves them untouched.
        """
        if deployment_ids is None:
            deployment_ids = await self.get_model_deployment_ids(model_id)
        if not deployment_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"LiteLLM model '{model_id}' not registered; cannot update.",
            )

        # Copy so we never mutate the caller's dict (it may back DBModel.litellm_params).
        params = dict(litellm_params or {})
        if "model" not in params:
            params["model"] = model_id

        result = {}
        try:
            async with httpx.AsyncClient(timeout=MODEL_HTTP_TIMEOUT) as client:
                for dep_id in deployment_ids:
                    model_info: dict = {"id": dep_id}
                    if access_groups is not None:
                        model_info["access_groups"] = access_groups
                    response = await client.post(
                        f"{self.api_url}/model/update",
                        headers={"Authorization": f"Bearer {self.master_key}"},
                        json={
                            "model_name": model_id,
                            "litellm_params": params,
                            "model_info": model_info,
                        },
                    )
                    response.raise_for_status()
                    result = response.json()
            return result
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            logger.error("Failed to update model %s in LiteLLM: %s", model_id, error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update LiteLLM model: {error_msg}",
            )

    async def delete_model(self, model_id: str, deployment_ids: Optional[list[str]] = None) -> None:
        """
        Delete/deregister a model in LiteLLM.
        Sends POST /model/delete per deployment id ({"id": ...} — LiteLLM does
        not accept model_name here). Absent deployments are treated as success.
        """
        if deployment_ids is None:
            deployment_ids = await self.get_model_deployment_ids(model_id)
        if not deployment_ids:
            logger.info("LiteLLM model %s already absent; continuing", model_id)
            return

        try:
            async with httpx.AsyncClient(timeout=MODEL_HTTP_TIMEOUT) as client:
                for dep_id in deployment_ids:
                    response = await client.post(
                        f"{self.api_url}/model/delete",
                        headers={"Authorization": f"Bearer {self.master_key}"},
                        json={"id": dep_id},
                    )
                    if response.status_code >= 400 and self._is_idempotent_litellm_error(
                        response.status_code,
                        response.text,
                        ["not found", "does not exist", "already deleted", "not registered"],
                    ):
                        logger.info("LiteLLM model %s (id=%s) already absent; continuing", model_id, dep_id)
                        continue
                    response.raise_for_status()
        except httpx.HTTPStatusError as e:
            _, error_msg, _ = self._parse_http_error(e)
            logger.error("Failed to delete model %s from LiteLLM: %s", model_id, error_msg)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete LiteLLM model: {error_msg}",
            )

