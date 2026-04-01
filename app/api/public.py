import asyncio
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.schemas.models import PublicModel
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


def _extract_model_data(item: dict[str, Any], fallback_region: str) -> PublicModel:
    model_info = item.get("model_info", {})
    litellm_params = item.get("litellm_params", {})

    model_id = item.get("model_name") or model_info.get("key") or "unknown"
    display_name = _to_display_name(model_id)
    provider = _infer_provider(item)
    region = litellm_params.get("aws_region_name") or fallback_region
    model_type = model_info.get("mode") or "other"
    context_length = model_info.get("max_input_tokens")

    return PublicModel(
        model_id=model_id,
        display_name=display_name,
        provider=provider,
        region=region,
        type=model_type,
        context_length=context_length,
        status="ga",
    )


async def _fetch_region_models(
    service: LiteLLMService, region_name: str
) -> list[PublicModel]:
    async with _REGION_SEMAPHORE:
        try:
            model_info = await asyncio.wait_for(
                service.get_model_info(), timeout=_REGION_TIMEOUT
            )
            return [
                _extract_model_data(item, region_name)
                for item in model_info.get("data", [])
            ]
        except (httpx.RequestError, HTTPException, asyncio.TimeoutError) as exc:
            logger.warning(
                "Region %s unavailable for /public/models: %s",
                region_name,
                str(exc),
            )
            return [
                PublicModel(
                    model_id="unavailable",
                    display_name=f"{region_name} unavailable",
                    provider="other",
                    region=region_name,
                    type="other",
                    context_length=None,
                    status="unavailable",
                )
            ]


@router.get("/models", response_model=list[PublicModel])
@router.get("/models/", response_model=list[PublicModel])
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
            _fetch_region_models(
                LiteLLMService(
                    api_url=region.litellm_api_url, api_key=region.litellm_api_key
                ),
                region.name,
            )
            for region in regions
        ]
        results = await asyncio.gather(*tasks)
        all_models = [model for region_models in results for model in region_models]

        _models_cache["data"] = all_models
        _models_cache["expires_at"] = datetime.now(UTC) + _CACHE_TTL

    return _models_cache["data"]
