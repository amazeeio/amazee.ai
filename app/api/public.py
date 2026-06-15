import asyncio
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user_from_auth
from app.db.database import get_db
from app.db.models import DBRegion, DBTeamRegion, DBUser
from app.schemas.models import (
    BedrockMissingModel,
    ProviderMissingModelsReport,
    ProviderRegionMissingModels,
    PublicModelCapabilities,
    PublicModelManufacturer,
    PublicModelPricing,
    PublicModelSummary,
    PublicRegionModels,
)
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public"])
protected_router = APIRouter(tags=["models"])

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


def _to_display_name(model_id: str, aliases: list[str] | None = None) -> str:
    """Convert a model_id to a human-friendly display name.

    If aliases contain a dotted version number (e.g. "claude-4.7"), use that
    to produce "Claude 4.7" instead of "Claude 4 7".
    """
    # Build a mapping of hyphenated number sequences to dotted equivalents
    # by inspecting aliases for dotted versions.
    dot_replacements: dict[str, str] = {}
    if aliases:
        for alias in aliases:
            # Find dotted number patterns like "4.7", "3.5", "1.5.2"
            for m in re.finditer(r"\d+(?:\.\d+)+", alias):
                dotted = m.group(0)
                # The equivalent hyphenated form: "4.7" -> "4-7"
                hyphenated = dotted.replace(".", "-")
                dot_replacements[hyphenated] = dotted

    # Replace hyphenated number sequences with dotted versions before splitting.
    # Use word-boundary anchors so that e.g. "3-5" does not corrupt "123-5".
    modified_id = model_id
    for hyphenated, dotted in sorted(
        dot_replacements.items(), key=lambda x: -len(x[0])
    ):
        modified_id = re.sub(
            r"(?<![0-9])" + re.escape(hyphenated) + r"(?![0-9])",
            dotted,
            modified_id,
        )

    words = re.split(r"[-_]+", modified_id)
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


_MANUFACTURER_RULES: list[dict[str, str | None]] = [
    {"keyword": "claude", "name": "Anthropic", "website": "https://www.anthropic.com"},
    {
        "keyword": "gemini",
        "name": "Google",
        "website": "https://deepmind.google/models",
    },
    {"keyword": "gemma", "name": "Google", "website": "https://deepmind.google/models"},
    {"keyword": "gpt", "name": "OpenAI", "website": "https://openai.com"},
    {"keyword": "mistral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "pixtral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "mixtral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "ministral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "devstral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "magistral", "name": "Mistral AI", "website": "https://mistral.ai"},
    {"keyword": "deepseek", "name": "DeepSeek", "website": "https://www.deepseek.com"},
    {"keyword": "llama", "name": "Meta", "website": "https://ai.meta.com/llama"},
    {"keyword": "kimi", "name": "Moonshot", "website": "https://www.moonshot.cn"},
    {
        "keyword": "qwen",
        "name": "Alibaba",
        "website": "https://www.alibabacloud.com/en/solutions/generative-ai/qwen",
    },
    {
        "keyword": "titan",
        "name": "Amazon",
        "website": "https://aws.amazon.com/bedrock/titan",
    },
    # Provider-based fallbacks (must be last — match on provider string only)
    {"keyword": "openai", "name": "OpenAI", "website": "https://openai.com"},
    {
        "keyword": "anthropic",
        "name": "Anthropic",
        "website": "https://www.anthropic.com",
    },
    {
        "keyword": "google",
        "name": "Google",
        "website": "https://deepmind.google/models",
    },
    {"keyword": "meta", "name": "Meta", "website": "https://ai.meta.com/llama"},
]


def _infer_manufacturer(
    model_id: str, item: dict[str, Any]
) -> PublicModelManufacturer | None:
    model_info = item.get("model_info", {})
    provider = str(model_info.get("litellm_provider") or "").lower()
    normalized_model_id = model_id.lower()

    name: str | None = None
    website: str | None = None

    for rule in _MANUFACTURER_RULES:
        keyword = str(rule["keyword"])
        if keyword in normalized_model_id or keyword in provider:
            name = rule["name"]
            website = rule["website"]
            break

    if name is None:
        return None

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

    aliases = _extract_aliases(item, model_id)

    return PublicModelSummary(
        model_id=model_id,
        display_name=_to_display_name(model_id, aliases),
        aliases=aliases,
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
    visible_groups = list(public_groups)

    if user:
        is_admin = bool(user.is_admin)
        team_id = user.team_id

        if is_admin:
            # Admins see all public regions (already in public_groups) plus all
            # active dedicated regions.  Only fetch the dedicated ones from LiteLLM;
            # public regions are reused from the global public_groups cache.
            cache_key = _ADMIN_CACHE_KEY
        elif team_id:
            # Team members see only their team's explicitly assigned regions.
            # Dedicated assigned regions are fetched from LiteLLM and cached;
            # public assigned regions are filtered from the global public_groups cache.
            cache_key = str(team_id)
        else:
            # Authenticated user without a team: same visibility as unauthenticated.
            cache_key = None

        if cache_key is not None:
            dedicated_to_fetch: list | None = None
            public_names_to_cache: frozenset[str] | None = None
            async with _dedicated_cache_lock:
                _evict_stale_dedicated_entries()
                entry = _dedicated_cache["by_team"].get(cache_key)
                if entry is not None and _dedicated_cache["team_expires"].get(
                    cache_key, datetime.min.replace(tzinfo=UTC)
                ) > datetime.now(UTC):
                    dedicated_groups = entry["dedicated"]
                    public_names = entry["public_names"]
                else:
                    # Query only dedicated regions for LiteLLM fetching; also
                    # collect assigned public region names to filter public_groups.
                    if is_admin:
                        dedicated_to_fetch = (
                            db.query(DBRegion)
                            .filter(
                                DBRegion.is_active.is_(True),
                                DBRegion.is_dedicated.is_(True),
                            )
                            .all()
                        )
                        public_names_to_cache = None  # admins see all public groups
                    else:
                        assigned = (
                            db.query(DBRegion)
                            .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
                            .filter(
                                DBTeamRegion.team_id == team_id,
                                DBRegion.is_active.is_(True),
                            )
                            .all()
                        )
                        dedicated_to_fetch = [r for r in assigned if r.is_dedicated]
                        public_names_to_cache = frozenset(
                            r.name for r in assigned if not r.is_dedicated
                        )
                    # Serve stale data to concurrent requests while refresh is in flight.
                    stale_entry = _dedicated_cache["by_team"].get(cache_key, {})
                    dedicated_groups = stale_entry.get("dedicated", [])
                    public_names = stale_entry.get("public_names")

            if dedicated_to_fetch is not None:
                if dedicated_to_fetch:
                    fetch_tasks = [
                        _fetch_region_model_group(
                            LiteLLMService(
                                api_url=region.litellm_api_url,
                                api_key=region.litellm_api_key,
                            ),
                            region.name,
                        )
                        for region in dedicated_to_fetch
                    ]
                    dedicated_groups = list(await asyncio.gather(*fetch_tasks))
                else:
                    dedicated_groups = []

                async with _dedicated_cache_lock:
                    _dedicated_cache["by_team"][cache_key] = {
                        "dedicated": dedicated_groups,
                        "public_names": public_names_to_cache,
                    }
                    _dedicated_cache["team_expires"][cache_key] = (
                        datetime.now(UTC) + _CACHE_TTL
                    )
                public_names = public_names_to_cache

            # Combine public groups (filtered by visibility) with dedicated groups.
            if is_admin:
                visible_groups = list(public_groups) + dedicated_groups
            else:
                visible_groups = [
                    g
                    for g in public_groups
                    if public_names is None or g.region in public_names
                ] + dedicated_groups

    # Signal to CacheControlMiddleware whether response contains user-specific data.
    request.state._public_models_is_authenticated = user is not None

    return _filter_region_groups_by_alias(visible_groups, alias_filters)


# ---------------------------------------------------------------------------
# /models/missing/{provider}
# ---------------------------------------------------------------------------
#
# Reports models available in an upstream hyperscaler catalog that are NOT
# yet deployed to any of the LiteLLM regions known to this backend.  This is
# the "we should add this model" feed.  The original implementation lived in
# amazeeai-k0rdent-clusters and parsed the raw ClusterDeployment YAML; here
# we infer the deployed set live from `LiteLLMService.get_model_info()` for
# each region, which gives us the same `litellm_params.model` strings (e.g.
# ``bedrock/us.anthropic.claude-...``).
#
# Per-provider extension:
#   /models/missing/aws     - implemented (Amazon Bedrock)
#   /models/missing/google  - 501, planned (Google Vertex)
#   /models/missing/azure   - 501, planned (Azure Foundry)
#
# Adding a new provider means writing one ``_build_<provider>_missing_report``
# helper and wiring it into ``_PROVIDER_BUILDERS``; the dispatcher and
# response shape stay the same.

# AWS Bedrock region groups, mirroring the k0rdent script.  Each "region
# group" is a market code that maps a provider ID prefix
# (``bedrock/<prefix>.modelId``) back to its upstream AWS region.
_AWS_REGION_GROUPS: dict[str, dict[str, str]] = {
    "US": {"upstream_region": "us-east-1", "provider_prefix": "us."},
    "EU": {"upstream_region": "eu-central-1", "provider_prefix": "eu."},
    "AU": {"upstream_region": "ap-southeast-2", "provider_prefix": "au."},
}

_BEDROCK_CATALOG_TTL = timedelta(hours=1)
_bedrock_catalog_lock = asyncio.Lock()
_bedrock_catalog_cache: dict[str, Any] = {
    "url": None,
    "expires_at": datetime.min.replace(tzinfo=UTC),
    "data": None,
}


async def _fetch_bedrock_catalog(url: str) -> list[dict[str, Any]]:
    """Fetch the upstream Bedrock model catalog, with a small in-memory cache.

    The catalog is on the order of a few hundred KB and changes infrequently,
    so a 1h TTL is plenty.  Cache key includes the URL so overrides bypass
    stale data automatically.
    """
    now = datetime.now(UTC)
    if (
        _bedrock_catalog_cache["url"] == url
        and _bedrock_catalog_cache["expires_at"] > now
        and _bedrock_catalog_cache["data"] is not None
    ):
        return _bedrock_catalog_cache["data"]

    async with _bedrock_catalog_lock:
        now = datetime.now(UTC)
        if (
            _bedrock_catalog_cache["url"] == url
            and _bedrock_catalog_cache["expires_at"] > now
            and _bedrock_catalog_cache["data"] is not None
        ):
            return _bedrock_catalog_cache["data"]

        timeout = settings.BEDROCK_MISSING_MODELS_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Upstream Bedrock catalog at {url} returned non-JSON response: {exc}"
                    ),
                ) from exc

        if not isinstance(data, list):
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Upstream Bedrock catalog at {url} did not return a JSON array"
                ),
            )

        _bedrock_catalog_cache["url"] = url
        _bedrock_catalog_cache["data"] = data
        _bedrock_catalog_cache["expires_at"] = now + _BEDROCK_CATALOG_TTL
        return data


def _build_available_aws_models_by_group(
    upstream_models: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, str]]]:
    """Group upstream Bedrock models by AWS region group, keyed by ``modelId``.

    Mirrors the k0rdent script: only ACTIVE models (or models that don't
    declare a lifecycle status) are considered, and a model is "available"
    in a group if its ``regions`` list contains the group's AWS region.
    """
    available: dict[str, dict[str, dict[str, str]]] = {
        group: {} for group in _AWS_REGION_GROUPS
    }

    for model in upstream_models:
        if not isinstance(model, dict):
            continue
        model_id = model.get("modelId")
        if not isinstance(model_id, str) or not model_id:
            continue

        lifecycle = model.get("modelLifecycle") or {}
        status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
        if status and status != "ACTIVE":
            continue

        regions_raw = model.get("regions")
        if not isinstance(regions_raw, (list, tuple)):
            regions_raw = []
        regions = {r for r in regions_raw if isinstance(r, str)}

        for group, details in _AWS_REGION_GROUPS.items():
            if details["upstream_region"] in regions:
                available[group][model_id] = {
                    "model_id": model_id,
                    "model_name": str(model.get("modelName") or model_id),
                    "provider_name": str(model.get("providerName") or "Unknown"),
                }

    return available


def _normalize_bedrock_provider_id(
    provider_model_id: str, provider_prefix: str
) -> str | None:
    """Strip ``bedrock/<prefix>.`` off a LiteLLM provider model id.

    Returns ``None`` when the id isn't a bedrock id at all so the caller
    can ignore non-bedrock providers (Vertex, Azure, OpenAI, etc.).
    """
    if not isinstance(provider_model_id, str):
        return None
    if not provider_model_id.startswith("bedrock/"):
        return None

    normalized = provider_model_id.split("/", 1)[1]
    if normalized.startswith(provider_prefix):
        normalized = normalized[len(provider_prefix) :]
    return normalized


async def _collect_region_bedrock_models(
    region: DBRegion,
) -> tuple[str, dict[str, set[str]], dict[str, set[str]]]:
    """Return ``(region_name, {group: {normalized_id, ...}}, {group: {all_matchable_ids, ...}})``.

    The first dict contains only the canonical normalized model IDs (used for
    counting deployed models).  The second dict adds aliases extracted from
    LiteLLM model metadata (used for matching against upstream catalog IDs).

    Failures are logged and produce empty sets so a single broken region can't
    take down the whole report.  We deliberately do NOT short-circuit when a
    region returns zero models — that's a legitimate state.
    """
    configured: dict[str, set[str]] = {group: set() for group in _AWS_REGION_GROUPS}
    matchable: dict[str, set[str]] = {group: set() for group in _AWS_REGION_GROUPS}

    service = LiteLLMService(
        api_url=region.litellm_api_url,
        api_key=region.litellm_api_key,
    )
    try:
        async with _REGION_SEMAPHORE:
            model_info = await asyncio.wait_for(
                service.get_model_info(),
                timeout=settings.BEDROCK_MISSING_MODELS_TIMEOUT_SECONDS,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "Region %s unavailable for /models/missing/aws: %s",
            region.name,
            exc,
        )
        return region.name, configured, matchable

    for item in model_info.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        params = item.get("litellm_params") or {}
        provider_model_id = params.get("model") if isinstance(params, dict) else None
        if not isinstance(provider_model_id, str) or not provider_model_id:
            continue

        for group, details in _AWS_REGION_GROUPS.items():
            normalized = _normalize_bedrock_provider_id(
                provider_model_id, details["provider_prefix"]
            )
            if normalized is None:
                continue
            # Only count a deployed bedrock model toward a group when its
            # provider id explicitly carries that group's prefix
            # (e.g. ``bedrock/us.anthropic...`` -> US).  Unprefixed bedrock
            # ids can't be safely attributed without per-region AWS-region
            # metadata, so we leave them out of every group rather than
            # double-count and hide real gaps.
            if provider_model_id.startswith(f"bedrock/{details['provider_prefix']}"):
                configured[group].add(normalized)
                matchable[group].add(normalized)
                # Also add all aliases (lowercased) so that models deployed
                # under a short name (e.g. "qwen3-32b") are matched against
                # their full upstream Bedrock model ID
                # (e.g. "qwen.qwen3-32b-v1:0") which appears in the aliases.
                model_info = item.get("model_info")
                model_info_dict = model_info if isinstance(model_info, dict) else {}
                model_name = item.get("model_name") or model_info_dict.get("key") or ""
                if model_name:
                    for alias in _extract_aliases(item, model_name):
                        matchable[group].add(alias)

    return region.name, configured, matchable


async def _build_aws_missing_report(
    db: Session, user: DBUser | None
) -> ProviderMissingModelsReport:
    """Build the AWS Bedrock missing-models report.

    Authenticated non-admin callers see only public regions in the
    "configured" set. Authenticated system admins additionally include
    private/dedicated regions so the report reflects the true deployed
    surface area.
    """
    include_private = bool(user and user.is_admin)

    upstream_models = await _fetch_bedrock_catalog(settings.BEDROCK_MODELS_URL)
    available_by_group = _build_available_aws_models_by_group(upstream_models)

    region_query = db.query(DBRegion).filter(DBRegion.is_active.is_(True))
    if not include_private:
        region_query = region_query.filter(DBRegion.is_dedicated.is_(False))
    regions = region_query.all()

    configured_by_group: dict[str, set[str]] = {
        group: set() for group in _AWS_REGION_GROUPS
    }
    matchable_by_group: dict[str, set[str]] = {
        group: set() for group in _AWS_REGION_GROUPS
    }
    contributing_regions_by_group: dict[str, set[str]] = {
        group: set() for group in _AWS_REGION_GROUPS
    }

    if regions:
        per_region = await asyncio.gather(
            *(_collect_region_bedrock_models(region) for region in regions)
        )
        for region_name, region_configured, region_matchable in per_region:
            for group, ids in region_configured.items():
                if ids:
                    configured_by_group[group].update(ids)
                    matchable_by_group[group].update(region_matchable[group])
                    contributing_regions_by_group[group].add(region_name)

    region_groups: list[ProviderRegionMissingModels] = []
    for group, details in _AWS_REGION_GROUPS.items():
        available = available_by_group[group]
        configured_ids = configured_by_group[group]
        matchable_ids = matchable_by_group[group]
        # Compare case-insensitively: aliases in matchable_ids are lowercased
        # by _extract_aliases/_normalize_alias, so lowercase the upstream keys
        # when computing the set difference.
        matchable_lower = {mid.lower() for mid in matchable_ids}
        missing_ids = sorted(
            mid for mid in available if mid.lower() not in matchable_lower
        )
        missing_models = [
            BedrockMissingModel(**available[model_id]) for model_id in missing_ids
        ]
        region_groups.append(
            ProviderRegionMissingModels(
                region_group=group,
                upstream_region=details["upstream_region"],
                regions=sorted(contributing_regions_by_group[group]),
                available_model_count=len(available),
                configured_model_count=len(configured_ids),
                missing_model_count=len(missing_models),
                missing_models=missing_models,
            )
        )

    return ProviderMissingModelsReport(
        provider="aws",
        generated_at=datetime.now(UTC),
        models_url=settings.BEDROCK_MODELS_URL,
        is_authenticated=user is not None,
        region_groups=region_groups,
    )


# Provider dispatcher.  Adding a new provider is a single line here plus a
# helper above; the endpoint shape and response schema are shared.
_PROVIDER_BUILDERS: dict[str, Any] = {
    "aws": _build_aws_missing_report,
    # "google": _build_google_missing_report,   # planned
    # "azure": _build_azure_missing_report,     # planned
}
_KNOWN_PROVIDERS: set[str] = {"aws", "google", "azure"}


@protected_router.get(
    "/models/missing/{provider}",
    response_model=ProviderMissingModelsReport,
    summary="Models available upstream at a hyperscaler but not yet deployed",
)
async def list_missing_provider_models(
    provider: str,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
) -> ProviderMissingModelsReport:
    """Compare an upstream hyperscaler model catalog against models deployed
    to the LiteLLM regions known to this backend.

    ``provider`` is the hyperscaler to inspect: ``aws`` (implemented),
    ``google`` (planned), ``azure`` (planned).  Unknown providers return
    404; known-but-unimplemented providers return 501.
    """
    provider_key = provider.lower()
    if provider_key not in _KNOWN_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown provider '{provider}'. Supported: {sorted(_KNOWN_PROVIDERS)}"
            ),
        )

    builder = _PROVIDER_BUILDERS.get(provider_key)
    if builder is None:
        raise HTTPException(
            status_code=501,
            detail=(
                f"Provider '{provider_key}' is recognised but not yet "
                "implemented on this backend."
            ),
        )

    return await builder(db, current_user)
