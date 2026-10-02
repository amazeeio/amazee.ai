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
from app.registry.discovery import parse_model_list
from app.registry.litellm import fetch_json, parse_key
from app.registry.models import (
    DBRegistryCloudAvailability,
    DBRegistryLitellmVersion,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
    models_by_ident,
)
from app.registry.runs import record_run
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


class Availability:
    """Which cloud regions offer which models, from registry_cloud_availability."""

    def __init__(self, db: Session):
        self.call_types = {
            (r.provider, r.model_id, r.cloud_region): r.call_types
            for r in db.query(DBRegistryCloudAvailability)
        }
        self.models = {(p, m) for p, m, _ in self.call_types}
        self.regions: dict[str, set] = {}
        for provider, _, region in self.call_types:
            self.regions.setdefault(provider, set()).add(region)

    def available(self, provider: str, model_id: str, region: str | None) -> bool | None:
        # Unknown, not false, when no source covers the model or that region at
        # all: the community list has no eu-central-2, for example.
        if not region or region not in self.regions.get(provider, ()) or (provider, model_id) not in self.models:
            return None
        return (provider, model_id, region) in self.call_types

    def in_region_problem(self, provider: str, model_id: str, region: str | None, deployed_model: str) -> str | None:
        """Why an in-region id cannot be called on demand, or None.

        `needs_profile`: the region offers the model only through a geo
        profile (`us.x`). `provisioned_only`: only with bought throughput.
        """
        parsed = parse_key(deployed_model, provider)
        call_types = self.call_types.get((provider, model_id, region)) or []
        if provider != "bedrock" or not parsed or parsed[1] != "base" or "ON_DEMAND" in call_types:
            return None
        if "INFERENCE_PROFILE" in call_types:
            return "needs_profile"
        if "PROVISIONED" in call_types:
            return "provisioned_only"
        return None


def apply_support(
    db: Session,
    region: DBRegion,
    models: dict[tuple[str, str], int],
    proxy_models: set[tuple[str, str]],
    release_models: set[tuple[str, str]] | None,
    availability: Availability,
    cloud_regions: dict[str, str],
    now: datetime,
) -> dict:
    """Write one row per model for the region. `release_models` is None when
    the region's release list is unknown, and then so is `supported`."""
    existing = {
        row.model_id: row
        for row in db.query(DBRegistryModelSupport).filter_by(region_id=region.id)
    }
    priced_ids, supported_ids, unavailable_ids = set(), set(), set()
    for ident, model_id in models.items():
        priced = ident in proxy_models
        supported = None if release_models is None else ident in release_models
        region_available = availability.available(*ident, cloud_regions.get(ident[0]))
        if priced:
            priced_ids.add(model_id)
        if supported:
            supported_ids.add(model_id)
        if region_available is False:
            unavailable_ids.add(model_id)
        row = existing.get(model_id)
        if row is None:
            row = DBRegistryModelSupport(model_id=model_id, region_id=region.id)
            db.add(row)
        row.priced, row.supported, row.region_available = priced, supported, region_available
        row.checked_at = now

    idents = {model_id: ident for ident, model_id in models.items()}
    deployed = db.query(
        DBRegistryModelRegion.model_name, DBRegistryModelRegion.model_id, DBRegistryModelRegion.litellm_model
    ).filter_by(region_id=region.id, enabled=True).all()
    stats = {
        "priced": len(priced_ids),
        "unpriced": len(models) - len(priced_ids),
        # Deployed but not priced by the proxy: their spend is $0.
        "deployed_unpriced": sorted(name for name, mid, _ in deployed if mid not in priced_ids),
        # Deployed where the cloud region does not offer the model: calls fail.
        "deployed_region_unavailable": sorted(name for name, mid, _ in deployed if mid in unavailable_ids),
    }
    # In-region ids that fail with "on-demand throughput isn't supported".
    problems = {"needs_profile": [], "provisioned_only": []}
    for name, mid, litellm_model in deployed:
        if mid in idents:
            problem = availability.in_region_problem(
                *idents[mid], cloud_regions.get(idents[mid][0]), litellm_model
            )
            if problem:
                problems[problem].append(name)
    stats["deployed_needs_profile"] = sorted(problems["needs_profile"])
    stats["deployed_provisioned_only"] = sorted(problems["provisioned_only"])
    if release_models is not None:
        stats["supported"] = len(supported_ids)
        # Deployed models the proxy's release did not ship with: untested on it.
        stats["deployed_unsupported"] = sorted(
            name for name, mid, _ in deployed if mid not in supported_ids
        )
    return stats


def run_support_check(db: Session) -> dict:
    return record_run(db, STEP, lambda: _check_support(db))


def _check_support(db: Session) -> dict:
    stats: dict = {"regions": {}, "unreachable": {}}
    providers = {p.name for p in db.query(DBRegistryProvider)}
    models = {ident: model.id for ident, model in models_by_ident(db).items()}
    proxies = {p.region_id: p for p in db.query(DBRegistryProxy)}
    availability = Availability(db)
    releases = {
        row.version: row.payload
        for row in db.query(DBRegistryLitellmVersion).filter(DBRegistryLitellmVersion.payload.isnot(None))
    }
    release_models: dict[str, set] = {}
    now = datetime.now(UTC)
    for region in active_regions(db):
        url = region.litellm_api_url.rstrip("/") + PRICE_LIST_PATH
        try:
            price_list = fetch_json(url)
        except (httpx.HTTPError, ValueError) as e:
            # Keep the region's last result rather than guess.
            stats["unreachable"][region.name] = str(e)
            continue
        proxy_models = (
            set(parse_model_list(priced_entries(price_list), providers)[0])
            if isinstance(price_list, dict)
            else set()
        )
        if not proxy_models:
            # A broken or empty list must not mark every model unpriced.
            stats["unreachable"][region.name] = "price list has no priced models of our providers"
            continue
        proxy = proxies.get(region.id)
        version = proxy.litellm_version if proxy else None
        cloud_regions = proxy.cloud_regions if proxy else {}
        if version in releases and version not in release_models:
            release_models[version] = set(parse_model_list(releases[version], providers)[0])
        stats["regions"][region.name] = {
            "version": version,
            **apply_support(
                db, region, models, proxy_models, release_models.get(version), availability, cloud_regions, now
            ),
        }
    return stats
