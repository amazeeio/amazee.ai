import asyncio
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.security import get_current_user_from_auth
from app.db.database import get_db
from app.db.models import DBRegion, DBTeam, DBTeamRegion, DBUser
from app.schemas.models import (
    PublicModelCapabilities,
    PublicModelManufacturer,
    PublicModelPricing,
    PublicModelSummary,
    PublicRegionModels,
)
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public"])

_CACHE_TTL = timedelta(hours=1)
_REGION_TIMEOUT = 10.0  # seconds per-region request
_REGION_SEMAPHORE = asyncio.Semaphore(10)  # max concurrent region requests
_DEFAULT_PUBLIC_MODEL_PROFIT_MARGIN = 0.2
_cache_lock = asyncio.Lock()
_dedicated_cache_lock = asyncio.Lock()
_models_cache: dict[str, Any] = {
    "expires_at": datetime.min.replace(tzinfo=UTC),
    "data": [],
}
_dedicated_cache: dict[str, Any] = {
    # keyed by team_id (str) → list[PublicRegionModels]
    "by_team": {},
    "team_expires": {},  # team_id → expiry datetime
}
_ADMIN_CACHE_KEY = "__admin__"  # special key for admin all-dedicated-regions cache


def _evict_stale_dedicated_entries() -> None:
    """Remove expired entries from ``_dedicated_cache`` to prevent unbounded growth.

    Must be called while ``_dedicated_cache_lock`` is held.
    """
    now = datetime.now(UTC)
    expired_keys = [
        key
        for key, expires_at in _dedicated_cache["team_expires"].items()
        if expires_at <= now
    ]
    for key in expired_keys:
        _dedicated_cache["by_team"].pop(key, None)
        _dedicated_cache["team_expires"].pop(key, None)


def _infer_provider(item: dict[str, Any]) -> str:
    provider = item.get("model_info", {}).get("litellm_provider")
    if provider and isinstance(provider, str):
        lowered = provider.lower()
        if "bedrock" in lowered or "aws" in lowered:
            return "aws"
        if "azure" in lowered:
            return "azure"
        if "gcp" in lowered or "vertex" in lowered or "google" in lowered:
            return "gcp"
    return "other"


def _safe_float(value: Any) -> float | None:
    """Coerce *value* to float, returning None on any parse failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _per_million(cost_per_token: float | None) -> float | None:
    if cost_per_token is None:
        return None
    return cost_per_token * 1_000_000


def _apply_profit_margin(price: float | None, margin: float) -> float | None:
    if price is None:
        return None
    return price * (1 + margin)


def _to_display_name(model_id: str) -> str:
    words = re.split(r"[-_]+", model_id)
    return " ".join(
        word.upper() if word.isupper() else word.capitalize() for word in words if word
    )


def _normalize_alias(alias: str) -> str:
    return alias.strip().lower()


def _extract_aliases(item: dict[str, Any], model_id: str) -> list[str]:
    aliases: set[str] = {_normalize_alias(model_id)}
    model_info = item.get("model_info", {})

    for key in ("base_model", "key", "model"):
        value = model_info.get(key)
        if isinstance(value, str) and value.strip():
            aliases.add(_normalize_alias(value))

    if "/" in model_id:
        aliases.add(_normalize_alias(model_id.split("/", 1)[-1]))

    lower_id = model_id.lower()
    if match := re.match(r"^(gpt-\d+(?:\.\d+)?)", lower_id):
        aliases.add(match.group(1))
    if match := re.match(r"^(claude-\d+(?:[-.]\d+)?)", lower_id):
        claude_alias = match.group(1)
        aliases.add(claude_alias)
        if claude_alias.count("-") >= 2:
            parts = claude_alias.split("-", 2)
            aliases.add(f"{parts[0]}-{parts[1]}.{parts[2]}")
    if match := re.match(r"^(gemini-\d+(?:\.\d+)?)", lower_id):
        aliases.add(match.group(1))

    return sorted(aliases)


def _extract_release_date(model_id: str, model_info: dict[str, Any]) -> str | None:
    for key in ("release_date", "released_at"):
        value = model_info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if match := re.search(r"(20\d{2})(\d{2})(\d{2})$", model_id):
        year, month, day = match.groups()
        return f"{year}-{month}-{day}"
    return None


def _infer_manufacturer(model_id: str, item: dict[str, Any]) -> PublicModelManufacturer:
    model_info = item.get("model_info", {})
    provider = str(model_info.get("litellm_provider") or "").lower()
    normalized_model_id = model_id.lower()

    if normalized_model_id.startswith("gpt-") or "openai" in provider:
        name = "OpenAI"
        website = "https://openai.com"
    elif normalized_model_id.startswith("claude-") or "anthropic" in provider:
        name = "Anthropic"
        website = "https://www.anthropic.com"
    elif normalized_model_id.startswith("gemini-") or "google" in provider:
        name = "Google"
        website = "https://deepmind.google/models"
    elif normalized_model_id.startswith("llama-") or "meta" in provider:
        name = "Meta"
        website = "https://ai.meta.com/llama"
    else:
        name = "Unknown"
        website = None

    version = (
        model_info.get("version")
        or model_info.get("model_version")
        or model_info.get("api_version")
    )
    if not version:
        version = model_id

    release_date = _extract_release_date(model_id, model_info)
    return PublicModelManufacturer(
        name=name,
        website=website,
        version=str(version) if version else None,
        release_date=release_date,
        attribution=f"{name} model routed through LiteLLM",
    )


def _build_description(
    model_type: str,
    capabilities: PublicModelCapabilities,
    context_length: int | None,
) -> str:
    strengths: list[str] = []
    if capabilities.supports_reasoning:
        strengths.append("structured reasoning")
    if capabilities.supports_function_calling:
        strengths.append("tool/function calling")
    if capabilities.supports_vision:
        strengths.append("multimodal vision")
    if not strengths:
        strengths.append("general-purpose text generation")

    use_cases: list[str] = ["chat assistants", "summarisation"]
    if model_type == "embedding":
        use_cases = ["semantic search", "retrieval indexing"]
    elif model_type == "image_generation":
        use_cases = ["image creation", "creative ideation"]

    limitations: list[str] = ["output quality and latency vary by workload"]
    if context_length is None:
        limitations.append("context window is provider-dependent")
    else:
        limitations.append(f"context window capped at about {context_length} tokens")

    return (
        f"Strengths: {', '.join(strengths)}. "
        f"Ideal for: {', '.join(use_cases)}. "
        f"Limitations: {', '.join(limitations)}."
    )


def _extract_global_margin(config: Any) -> float | None:
    if not isinstance(config, dict):
        return None
    values = config.get("values")
    if isinstance(values, dict):
        margin = _safe_float(values.get("global"))
        if margin is not None:
            return margin
    return _safe_float(config.get("global"))


async def _resolve_profit_margin(service: LiteLLMService, region_name: str) -> float:
    try:
        margin_config_result: Any = service.get_cost_margin_config()
    except AttributeError:
        return _DEFAULT_PUBLIC_MODEL_PROFIT_MARGIN
    if asyncio.iscoroutine(margin_config_result):
        try:
            margin_config_result = await margin_config_result
        except (
            httpx.RequestError,
            HTTPException,
            asyncio.TimeoutError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning(
                "Region %s margin lookup failed for /public/models: %s. Falling back to %.2f",
                region_name,
                str(exc),
                _DEFAULT_PUBLIC_MODEL_PROFIT_MARGIN,
            )
            return _DEFAULT_PUBLIC_MODEL_PROFIT_MARGIN

    margin = _extract_global_margin(margin_config_result)
    if margin is None:
        return _DEFAULT_PUBLIC_MODEL_PROFIT_MARGIN
    return margin


def _extract_model_summary(
    item: dict[str, Any], profit_margin: float
) -> PublicModelSummary:
    model_info = item.get("model_info", {})
    model_id = item.get("model_name") or model_info.get("key") or "unknown"
    model_type = model_info.get("mode") or "other"
    context_length = model_info.get("max_input_tokens")
    input_cost_per_token = _apply_profit_margin(
        _safe_float(model_info.get("input_cost_per_token")), profit_margin
    )
    output_cost_per_token = _apply_profit_margin(
        _safe_float(model_info.get("output_cost_per_token")), profit_margin
    )
    cache_creation_input_cost_per_token = _apply_profit_margin(
        _safe_float(model_info.get("cache_creation_input_token_cost")), profit_margin
    )
    cache_creation_input_cost_above_1hr_per_token = _apply_profit_margin(
        _safe_float(model_info.get("cache_creation_input_token_cost_above_1hr")),
        profit_margin,
    )
    cache_read_input_cost_per_token = _apply_profit_margin(
        _safe_float(model_info.get("cache_read_input_token_cost")), profit_margin
    )
    capabilities = PublicModelCapabilities(
        supports_vision=bool(model_info.get("supports_vision")),
        supports_function_calling=bool(model_info.get("supports_function_calling")),
        supports_reasoning=bool(model_info.get("supports_reasoning")),
        supports_prompt_caching=bool(model_info.get("supports_prompt_caching")),
    )

    return PublicModelSummary(
        model_id=model_id,
        display_name=_to_display_name(model_id),
        aliases=_extract_aliases(item, model_id),
        metadata_raw=model_info.get("metadata") or item.get("metadata"),
        provider=_infer_provider(item),
        type=model_type,
        context_length=context_length,
        max_output_tokens=model_info.get("max_output_tokens"),
        description=_build_description(model_type, capabilities, context_length),
        manufacturer=_infer_manufacturer(model_id, item),
        capabilities=capabilities,
        pricing=PublicModelPricing(
            input_cost_per_token=input_cost_per_token,
            output_cost_per_token=output_cost_per_token,
            input_cost_per_million_tokens=_per_million(input_cost_per_token),
            output_cost_per_million_tokens=_per_million(output_cost_per_token),
            cache_creation_input_cost_per_million_tokens=_per_million(
                cache_creation_input_cost_per_token
            ),
            cache_creation_input_cost_above_1hr_per_million_tokens=_per_million(
                cache_creation_input_cost_above_1hr_per_token
            ),
            cache_read_input_cost_per_million_tokens=_per_million(
                cache_read_input_cost_per_token
            ),
        ),
    )


def _parse_alias_filters(alias: list[str] | None) -> set[str]:
    if not alias:
        return set()
    parsed: set[str] = set()
    for alias_entry in alias:
        for part in alias_entry.split(","):
            normalized = _normalize_alias(part)
            if normalized:
                parsed.add(normalized)
    return parsed


def _filter_region_groups_by_alias(
    region_groups: list[PublicRegionModels], alias_filters: set[str]
) -> list[PublicRegionModels]:
    if not alias_filters:
        return region_groups

    filtered_region_groups: list[PublicRegionModels] = []
    for region_group in region_groups:
        filtered_models = [
            model
            for model in region_group.models
            if alias_filters.intersection(set(model.aliases))
        ]
        filtered_region_groups.append(
            PublicRegionModels(
                region=region_group.region,
                status=region_group.status,
                models=filtered_models,
            )
        )
    return filtered_region_groups


async def _resolve_optional_user(request: Request, db: Session) -> DBUser | None:
    """Optionally resolve the authenticated user from the request.

    Reuses the existing ``get_current_user_from_auth`` helper (which handles
    API tokens, JWTs, ``request.state.user`` from AuthMiddleware, and
    ``last_used_at`` tracking).  Returns ``None`` when the request is
    unauthenticated or the token is invalid — never raises.
    """
    access_token = request.cookies.get("access_token")
    authorization = request.headers.get("authorization")

    # Short-circuit immediately when no credentials are present at all.
    if (
        not access_token
        and not authorization
        and not getattr(request.state, "user", None)
    ):
        return None

    try:
        return await get_current_user_from_auth(
            access_token=access_token,
            authorization=authorization,
            db=db,
            request=request,
        )
    except asyncio.CancelledError:
        raise
    except HTTPException as exc:
        # A 401 is the expected "unauthenticated" response; swallow silently.
        if exc.status_code != 401:
            logger.debug(
                "Optional auth resolution failed for /public/models (HTTP %s)",
                exc.status_code,
            )
        return None
    except Exception:
        logger.debug(
            "Optional auth resolution failed for /public/models", exc_info=True
        )
        return None


async def _fetch_region_model_group(
    service: LiteLLMService, region_name: str
) -> PublicRegionModels:
    async with _REGION_SEMAPHORE:
        try:
            model_info = await asyncio.wait_for(
                service.get_model_info(), timeout=_REGION_TIMEOUT
            )
            profit_margin = await asyncio.wait_for(
                _resolve_profit_margin(service, region_name),
                timeout=_REGION_TIMEOUT,
            )
            return PublicRegionModels(
                region=region_name,
                status="ga",
                models=[
                    _extract_model_summary(item, profit_margin)
                    for item in model_info.get("data", [])
                ],
            )
        except (httpx.RequestError, HTTPException, asyncio.TimeoutError) as exc:
            logger.warning(
                "Region %s unavailable for /public/models: %s",
                region_name,
                str(exc),
            )
            return PublicRegionModels(
                region=region_name,
                status="unavailable",
                models=[],
            )


@router.get("/models", response_model=list[PublicRegionModels])
async def list_public_models(
    request: Request,
    alias: list[str] | None = Query(
        default=None,
        description=(
            "Optional model alias filters. Repeat query parameter or pass "
            "comma-separated aliases, e.g. ?alias=gpt-4&alias=claude-3-5"
        ),
    ),
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    alias_filters = _parse_alias_filters(alias)

    # --- Public regions (cached globally) ---
    if _models_cache["expires_at"] > now:
        public_groups = _models_cache["data"]
    else:
        regions_to_fetch: list | None = None
        async with _cache_lock:
            now = datetime.now(UTC)
            if _models_cache["expires_at"] > now:
                public_groups = _models_cache["data"]
            else:
                regions_to_fetch = (
                    db.query(DBRegion)
                    .filter(
                        DBRegion.is_active.is_(True),
                        DBRegion.is_dedicated.is_(False),
                    )
                    .all()
                )
                # Serve stale data to concurrent requests while refresh is in flight.
                public_groups = list(_models_cache["data"])

        if regions_to_fetch is not None:
            tasks = [
                _fetch_region_model_group(
                    LiteLLMService(
                        api_url=region.litellm_api_url,
                        api_key=region.litellm_api_key,
                    ),
                    region.name,
                )
                for region in regions_to_fetch
            ]
            fetched_groups = list(await asyncio.gather(*tasks))
            async with _cache_lock:
                _models_cache["data"] = fetched_groups
                _models_cache["expires_at"] = datetime.now(UTC) + _CACHE_TTL
            public_groups = fetched_groups

    # --- Dedicated regions (optional, per-user) ---
    user = await _resolve_optional_user(request, db)
    dedicated_groups: list[PublicRegionModels] = []

    if user:
        is_admin = bool(user.is_admin)
        team_id = user.team_id

        if is_admin:
            # Admins see ALL dedicated regions across every team.
            cache_key = _ADMIN_CACHE_KEY
            query = db.query(DBRegion).filter(
                DBRegion.is_active.is_(True),
                DBRegion.is_dedicated.is_(True),
            )
        elif team_id:
            # Regular team member sees only their team's dedicated regions.
            cache_key = str(team_id)
            query = (
                db.query(DBRegion)
                .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
                .filter(
                    DBTeamRegion.team_id == team_id,
                    DBRegion.is_active.is_(True),
                    DBRegion.is_dedicated.is_(True),
                )
            )
        else:
            # Authenticated user without a team — no dedicated regions.
            cache_key = None
            query = None

        if cache_key and query is not None:
            dedicated_regions_to_fetch: list | None = None
            async with _dedicated_cache_lock:
                _evict_stale_dedicated_entries()
                if _dedicated_cache["team_expires"].get(
                    cache_key, datetime.min.replace(tzinfo=UTC)
                ) > datetime.now(UTC):
                    dedicated_groups = _dedicated_cache["by_team"].get(cache_key, [])
                else:
                    dedicated_regions_to_fetch = query.all()
                    # Serve stale data to concurrent requests while refresh is in flight.
                    dedicated_groups = list(
                        _dedicated_cache["by_team"].get(cache_key, [])
                    )

            if dedicated_regions_to_fetch is not None:
                if dedicated_regions_to_fetch:
                    fetch_tasks = [
                        _fetch_region_model_group(
                            LiteLLMService(
                                api_url=region.litellm_api_url,
                                api_key=region.litellm_api_key,
                            ),
                            region.name,
                        )
                        for region in dedicated_regions_to_fetch
                    ]
                    dedicated_groups = list(await asyncio.gather(*fetch_tasks))
                else:
                    dedicated_groups = []

                async with _dedicated_cache_lock:
                    _dedicated_cache["by_team"][cache_key] = dedicated_groups
                    _dedicated_cache["team_expires"][cache_key] = (
                        datetime.now(UTC) + _CACHE_TTL
                    )

        # Honour team-level hide_public_regions flag for non-admin users.
        if not is_admin and team_id:
            team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
            if team and team.hide_public_regions:
                public_groups = []

    # Signal to CacheControlMiddleware whether response contains user-specific data.
    request.state._public_models_is_authenticated = user is not None

    all_groups = list(public_groups) + dedicated_groups
    return _filter_region_groups_by_alias(all_groups, alias_filters)
