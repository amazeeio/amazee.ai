"""The Bedrock community model list: AWS regions, call types and lifecycle.

It mirrors AWS's own Bedrock model list and is the only public source of
which AWS regions offer each model. Its prices and limits are skipped:
LiteLLM's list has them for more models.
"""

from datetime import date

from app.registry import config
from app.registry.plugins.common import fetch_json

SOURCE = "bedrock_community"
ORDER = 20
PRICE_ROLE = "fill"  # it sends no prices


def _day(value) -> str | None:
    """`2026-09-08 17:00:00+00:00` -> `2026-09-08`; None when not a date, so
    one bad value drops that date and not the whole source."""
    try:
        return date.fromisoformat(str(value)[:10]).isoformat() if value else None
    except ValueError:
        return None


def transform(rows) -> dict:
    """A model runs on Bedrock in `regions` and on Bedrock Mantle in the model
    card's `mantleRegions`. A Mantle-only model lists its Mantle regions in
    `regions` too, and has no plain Bedrock availability.
    """
    if not isinstance(rows, list):
        raise ValueError("Bedrock catalog is not a list")
    models = []
    for row in rows:
        model_id = row.get("modelId") if isinstance(row, dict) else None
        if not model_id:
            raise ValueError("Bedrock catalog row without modelId")
        card = row.get("modelCard") or {}
        call_types = sorted(row.get("inferenceTypesSupported") or [])
        mantle_regions = set(card.get("mantleRegions") or [])
        if row.get("mantleOnly"):
            providers = {"bedrock_mantle": mantle_regions | set(row.get("regions") or [])}
        else:
            providers = {"bedrock": set(row.get("regions") or []), "bedrock_mantle": mantle_regions}
        life = row.get("modelLifecycle") or {}
        lifecycle = {
            "status": life.get("status"),
            "launched_at": _day(life.get("startOfLifeTime")),
            "legacy_at": _day(life.get("legacyTime")),
            "extended_access_until": _day(life.get("publicExtendedAccessTime")),
            "eol_date": _day(life.get("endOfLifeTime")),
        }
        for provider, regions in providers.items():
            if not regions:
                continue
            # Bedrock's call types do not apply to Mantle's own endpoint.
            types = call_types if provider == "bedrock" else []
            models.append(
                {
                    "provider": provider,
                    "model_id": model_id,
                    "lifecycle": lifecycle,
                    "regions": [{"cloud_region": r, "call_types": types} for r in sorted(regions)],
                }
            )
    return {"schema": 1, "source": SOURCE, "models": models}


def parse() -> dict:
    return transform(fetch_json(config.BEDROCK_CATALOG_URL))
