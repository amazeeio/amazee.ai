import asyncio
import logging
import re
import os
from datetime import datetime, timedelta, UTC
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.schemas.models import (
    PublicModelCapabilities,
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
_cache_lock = asyncio.Lock()
_models_cache: dict[str, Any] = {
    "expires_at": datetime.min.replace(tzinfo=UTC),
    "data": [],
}


def _get_litellm_model_info_key(region_key: str) -> str:
    return os.getenv("LITELLM_MASTER_KEY") or region_key


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


def _to_display_name(model_id: str) -> str:
    words = re.split(r"[-_]+", model_id)
    return " ".join(
        word.upper() if word.isupper() else word.capitalize() for word in words if word
    )


def _extract_model_summary(item: dict[str, Any]) -> PublicModelSummary:
    model_info = item.get("model_info", {})
    model_id = item.get("model_name") or model_info.get("key") or "unknown"

    return PublicModelSummary(
        model_id=model_id,
        display_name=_to_display_name(model_id),
        provider=_infer_provider(item),
        type=model_info.get("mode") or "other",
        context_length=model_info.get("max_input_tokens"),
        max_output_tokens=model_info.get("max_output_tokens"),
        capabilities=PublicModelCapabilities(
            supports_vision=bool(model_info.get("supports_vision")),
            supports_function_calling=bool(model_info.get("supports_function_calling")),
            supports_reasoning=bool(model_info.get("supports_reasoning")),
            supports_prompt_caching=bool(model_info.get("supports_prompt_caching")),
        ),
        pricing=PublicModelPricing(
            input_cost_per_token=model_info.get("input_cost_per_token"),
            output_cost_per_token=model_info.get("output_cost_per_token"),
        ),
    )


async def _fetch_region_model_group(
    service: LiteLLMService, region_name: str
) -> PublicRegionModels:
    async with _REGION_SEMAPHORE:
        try:
            model_info = await asyncio.wait_for(
                service.get_model_info(), timeout=_REGION_TIMEOUT
            )
            return PublicRegionModels(
                region=region_name,
                status="ga",
                models=[
                    _extract_model_summary(item) for item in model_info.get("data", [])
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
async def list_public_models(db: Session = Depends(get_db)):
    now = datetime.now(UTC)

    if _models_cache["expires_at"] > now:
        return _models_cache["data"]

    async with _cache_lock:
        now = datetime.now(UTC)
        if _models_cache["expires_at"] > now:
            return _models_cache["data"]

        regions = (
            db.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
            .all()
        )

        tasks = [
            _fetch_region_model_group(
                LiteLLMService(
                    api_url=region.litellm_api_url,
                    api_key=_get_litellm_model_info_key(region.litellm_api_key),
                ),
                region.name,
            )
            for region in regions
        ]
        region_groups = await asyncio.gather(*tasks)

        _models_cache["data"] = region_groups
        _models_cache["expires_at"] = datetime.now(UTC) + _CACHE_TTL

    return _models_cache["data"]
