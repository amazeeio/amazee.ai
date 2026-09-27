"""Daily read of the Bedrock community model list: regions, call types, lifecycle.

The list mirrors AWS's own Bedrock model list. It is the only public source
of which AWS regions offer each model. Its prices and limits are skipped:
LiteLLM's list has them for more models.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.discovery import finish_run
from app.registry.litellm import fetch_model_list
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModelLifecycle, DBRegistryRun

logger = logging.getLogger(__name__)

STEP = "bedrock-community"
SOURCE = "bedrock_community"


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def parse_catalog(rows: list) -> tuple[dict, dict]:
    """(availability, lifecycle) keyed like their tables, without the source.

    A model runs on Bedrock in `regions` and on Bedrock Mantle in the model
    card's `mantleRegions`. A Mantle-only model lists its Mantle regions in
    `regions` too, and has no plain Bedrock availability.
    """
    if not isinstance(rows, list):
        raise ValueError("Bedrock catalog is not a list")
    availability: dict[tuple[str, str, str], list] = {}
    lifecycle: dict[tuple[str, str], dict] = {}
    for row in rows:
        model_id = row.get("modelId") if isinstance(row, dict) else None
        if not model_id:
            raise ValueError("Bedrock catalog row without modelId")
        card = row.get("modelCard") or {}
        call_types = sorted(row.get("inferenceTypesSupported") or [])
        mantle_regions = set(card.get("mantleRegions") or [])
        if row.get("mantleOnly"):
            mantle_regions |= set(row.get("regions") or [])
            providers = {"bedrock_mantle": mantle_regions}
        else:
            providers = {"bedrock": set(row.get("regions") or []), "bedrock_mantle": mantle_regions}
        life = row.get("modelLifecycle") or {}
        for provider, regions in providers.items():
            if not regions:
                continue
            # Bedrock's call types do not apply to Mantle's own endpoint.
            types = call_types if provider == "bedrock" else []
            for region in regions:
                availability[(provider, model_id, region)] = types
            lifecycle[(provider, model_id)] = {
                "status": life.get("status"),
                "launched_at": _date(life.get("startOfLifeTime")),
                "legacy_at": _date(life.get("legacyTime")),
                "extended_access_until": _date(life.get("publicExtendedAccessTime")),
                "eol_date": _date(life.get("endOfLifeTime")),
            }
    return availability, lifecycle


def apply_catalog(db: Session, availability: dict, lifecycle: dict, today: date) -> dict:
    known = {
        (r.provider, r.model_id): r for r in db.query(DBRegistryModelLifecycle).filter_by(source=SOURCE)
    }
    # The models the last run saw; a bad download would drop most of them.
    latest = max((r.last_seen for r in known.values()), default=None)
    at_risk = {key for key, row in known.items() if row.last_seen == latest}
    kept = len(at_risk & lifecycle.keys())
    if at_risk and kept < len(at_risk) * config.MIN_KEPT_RATIO:
        raise RuntimeError(
            f"Bedrock catalog keeps {kept} of {len(at_risk)} known models; refusing to write"
        )

    stats = {"models": len(lifecycle), "regions": len(availability), "changed": 0, "regions_dropped": 0}
    for key, fields in lifecycle.items():
        row = known.get(key)
        if row is None:
            row = DBRegistryModelLifecycle(provider=key[0], model_id=key[1], source=SOURCE)
            db.add(row)
            stats["changed"] += 1
        elif any(getattr(row, k) != v for k, v in fields.items()):
            stats["changed"] += 1
        for k, v in fields.items():
            setattr(row, k, v)
        row.last_seen = today

    # Availability is "offered now", so a region the list dropped is deleted.
    # Lifecycle rows stay: they are history.
    stored = {
        (r.provider, r.model_id, r.cloud_region): r
        for r in db.query(DBRegistryCloudAvailability).filter_by(source=SOURCE)
    }
    for key, call_types in availability.items():
        row = stored.get(key)
        if row is None:
            db.add(
                DBRegistryCloudAvailability(
                    provider=key[0],
                    model_id=key[1],
                    cloud_region=key[2],
                    source=SOURCE,
                    call_types=call_types,
                    last_seen=today,
                )
            )
            continue
        row.call_types, row.last_seen = call_types, today
    for key, row in stored.items():
        if key not in availability:
            db.delete(row)
            stats["regions_dropped"] += 1
    return stats


def run_bedrock_catalog(db: Session, today: date | None = None) -> dict:
    run = DBRegistryRun(step=STEP, status="running")
    db.add(run)
    db.commit()
    try:
        availability, lifecycle = parse_catalog(fetch_model_list(config.BEDROCK_CATALOG_URL))
        stats = apply_catalog(db, availability, lifecycle, today or date.today())
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats

