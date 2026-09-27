"""Daily check of which models each region's proxy can price and its release knows.

A proxy prices requests from the list it downloaded from `main` when it last
started. Comparing that list with ours shows models added since the restart,
and deployed models the proxy cannot price, whose spend it logs as $0.
"""

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry.discovery import finish_run, parse_model_list
from app.registry.litellm import fetch_model_list
from app.registry.models import (
    DBRegistryLitellmVersion,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
    DBRegistryRun,
)
from app.registry.versions import active_regions

logger = logging.getLogger(__name__)

STEP = "support-check"
PRICE_LIST_PATH = "/public/litellm_model_cost_map"


def priced_entries(price_list: dict) -> dict:
    """Keep entries with a price. Zero counts: some models are free.

    A proxy adds an entry for each of its deployments, with a provider but no
    price, so being listed alone does not mean the proxy can price the model.
    """
    return {
        key: entry
        for key, entry in price_list.items()
        if isinstance(entry, dict)
        and any("cost_per" in field and value is not None for field, value in entry.items())
    }


def apply_support(
    db: Session,
    region: DBRegion,
    models: dict[tuple[str, str], int],
    proxy_models: set[tuple[str, str]],
    release_models: set[tuple[str, str]] | None,
    now: datetime,
) -> dict:
    """Write one row per model for the region. `release_models` is None when
    the region's release list is unknown, and then so is `supported`."""
    existing = {
        row.model_id: row
        for row in db.query(DBRegistryModelSupport).filter_by(region_id=region.id)
    }
    priced_ids, supported_ids = set(), set()
    for ident, model_id in models.items():
        priced = ident in proxy_models
        supported = None if release_models is None else ident in release_models
        if priced:
            priced_ids.add(model_id)
        if supported:
            supported_ids.add(model_id)
        row = existing.get(model_id)
        if row is None:
            row = DBRegistryModelSupport(model_id=model_id, region_id=region.id)
            db.add(row)
        row.priced, row.supported, row.checked_at = priced, supported, now

    deployed = db.query(DBRegistryModelRegion.model_name, DBRegistryModelRegion.model_id).filter_by(
        region_id=region.id, enabled=True
    )
    stats = {
        "priced": len(priced_ids),
        "unpriced": len(models) - len(priced_ids),
        # Deployed but not priced by the proxy: their spend is $0.
        "deployed_unpriced": sorted(name for name, mid in deployed if mid not in priced_ids),
    }
    if release_models is not None:
        stats["supported"] = len(supported_ids)
        # Deployed models the proxy's release did not ship with: untested on it.
        stats["deployed_unsupported"] = sorted(
            name for name, mid in deployed if mid not in supported_ids
        )
    return stats


def run_support_check(db: Session) -> dict:
    run = DBRegistryRun(step=STEP, status="running")
    db.add(run)
    db.commit()
    stats: dict = {"regions": {}, "unreachable": {}}
    try:
        providers = {p.name for p in db.query(DBRegistryProvider)}
        models = {
            (provider, row.model_id): row.id
            for row, provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
        }
        versions = {p.region_id: p.litellm_version for p in db.query(DBRegistryProxy)}
        releases = {
            row.version: row.payload
            for row in db.query(DBRegistryLitellmVersion).filter(DBRegistryLitellmVersion.payload.isnot(None))
        }
        release_models: dict[str, set] = {}
        now = datetime.now(UTC)
        for region in active_regions(db):
            url = region.litellm_api_url.rstrip("/") + PRICE_LIST_PATH
            try:
                price_list = fetch_model_list(url)
            except (httpx.HTTPError, ValueError) as e:
                # Keep the region's last result rather than guess.
                stats["unreachable"][region.name] = str(e)
                continue
            proxy_models = set(parse_model_list(priced_entries(price_list), providers)[0])
            version = versions.get(region.id)
            if version in releases and version not in release_models:
                release_models[version] = set(parse_model_list(releases[version], providers)[0])
            stats["regions"][region.name] = {
                "version": version,
                **apply_support(db, region, models, proxy_models, release_models.get(version), now),
            }
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats
