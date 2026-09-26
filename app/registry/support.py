"""Daily check of which models each region's proxy can price.

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
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryRun,
)

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
    now: datetime,
) -> dict:
    existing = {
        row.model_id: row
        for row in db.query(DBRegistryModelSupport).filter_by(region_id=region.id)
    }
    priced_ids = set()
    for ident, model_id in models.items():
        priced = ident in proxy_models
        if priced:
            priced_ids.add(model_id)
        row = existing.get(model_id)
        if row is None:
            db.add(
                DBRegistryModelSupport(
                    model_id=model_id, region_id=region.id, priced=priced, checked_at=now
                )
            )
        else:
            row.priced, row.checked_at = priced, now

    unpriced = sorted(
        name
        for name, model_id in db.query(DBRegistryModelRegion.model_name, DBRegistryModelRegion.model_id)
        .filter_by(region_id=region.id, enabled=True)
        if model_id not in priced_ids
    )
    return {
        "priced": len(priced_ids),
        "unpriced": len(models) - len(priced_ids),
        # Deployed but not priced by the proxy: their spend is $0.
        "deployed_unpriced": unpriced,
    }


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
        regions = (
            db.query(DBRegion)
            .filter(DBRegion.is_active.is_(True), DBRegion.litellm_api_url.isnot(None))
            .order_by(DBRegion.id)
            .all()
        )
        now = datetime.now(UTC)
        for region in regions:
            url = region.litellm_api_url.rstrip("/") + PRICE_LIST_PATH
            try:
                price_list = fetch_model_list(url)
            except (httpx.HTTPError, ValueError) as e:
                # Keep the region's last result rather than guess.
                stats["unreachable"][region.name] = str(e)
                continue
            proxy_models = set(parse_model_list(priced_entries(price_list), providers)[0])
            stats["regions"][region.name] = apply_support(db, region, models, proxy_models, now)
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats
