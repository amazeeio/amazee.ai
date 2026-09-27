"""One-time import of a proxy's live deployments into the registry.

After the import the registry owns what runs on the proxy, so a second
import is refused: it would overwrite registry decisions with proxy state.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry.discovery import entry_fields, finish_run
from app.registry.litellm import ProxyClient, deployment_provider, split_model
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
    DBRegistryRun,
)

logger = logging.getLogger(__name__)

STEP = "proxy-import"


class AlreadyImported(Exception):
    pass


def _get_or_create(db: Session, model, defaults: dict | None = None, **keys):
    row = db.query(model).filter_by(**keys).first()
    if row is None:
        row = model(**keys, **(defaults or {}))
        db.add(row)
        db.flush()
    return row


def _proxy_model_fields(info: dict) -> dict:
    fields = entry_fields(info)
    # /model/info fills prices with 0 for a model LiteLLM does not know.
    # Stored as 0 it would look free, so it is stored as unknown.
    if not any(fields["prices"].values()):
        fields["input_cost_per_token"] = fields["output_cost_per_token"] = None
        fields["prices"] = {}
    return fields


def _proxy_cloud_regions(deployments: list[dict], credential_regions: dict[str, str]) -> dict:
    """The cloud region each provider on the proxy calls, first one seen wins."""
    regions: dict[str, str] = {}
    for dep in deployments:
        params = dep.get("litellm_params") or {}
        provider = deployment_provider(params)
        if not provider or provider in regions:
            continue
        if provider.startswith("bedrock"):
            region = params.get("aws_region_name") or credential_regions.get(
                params.get("litellm_credential_name")
            )
        elif provider == "vertex_ai":
            region = params.get("vertex_location")
        else:
            region = None
        if region:
            regions[provider] = region
    return regions


def import_deployments(
    db: Session,
    region: DBRegion,
    deployments: list[dict],
    version: str | None,
    credential_regions: dict[str, str],
    today: date | None = None,
) -> dict:
    proxy = db.get(DBRegistryProxy, region.id)
    if proxy is not None and proxy.imported_at is not None:
        raise AlreadyImported(f"region {region.name} was imported at {proxy.imported_at}")
    today = today or date.today()

    stats = {"deployments": len(deployments), "imported": 0, "skipped": []}
    for dep in deployments:
        params = dep.get("litellm_params") or {}
        info = dep.get("model_info") or {}
        litellm_model = params.get("model") or ""
        provider_name = deployment_provider(params)
        # Azure's model string names our deployment; base_model names the model.
        source_model = info.get("base_model") or litellm_model
        split = split_model(source_model, provider_name) if provider_name else None
        if split is None:
            # With no provider prefix LiteLLM guesses the provider; we do not.
            stats["skipped"].append(dep.get("model_name"))
            continue
        provider = _get_or_create(db, DBRegistryProvider, name=provider_name)
        model = _get_or_create(
            db,
            DBRegistryModel,
            provider_id=provider.id,
            model_id=split[0],
            defaults={
                **_proxy_model_fields(info),
                "status": "active",
                "source": "proxy",
                "first_seen": today,
                "last_seen": today,
            },
        )
        # Only a price in litellm_params is set on the proxy. model_info
        # carries LiteLLM's own price merged in, which the list already has.
        fields = entry_fields(params)
        model_region = DBRegistryModelRegion(
            model_id=model.id,
            region_id=region.id,
            model_name=dep.get("model_name") or litellm_model,
            litellm_model=litellm_model,
            litellm_deployment_id=info.get("id"),
            input_cost_per_token=fields["input_cost_per_token"],
            output_cost_per_token=fields["output_cost_per_token"],
            prices=fields["prices"],
            enabled=True,
        )
        db.add(model_region)
        db.flush()
        for slug in dict.fromkeys(info.get("access_groups") or []):
            group = _get_or_create(db, DBRegistryAccessGroup, slug=slug, defaults={"label": slug})
            db.add(DBRegistryModelRegionGroup(model_region_id=model_region.id, access_group_id=group.id))
        stats["imported"] += 1

    if proxy is None:
        proxy = DBRegistryProxy(region_id=region.id)
        db.add(proxy)
    proxy.litellm_version = version
    proxy.cloud_regions = _proxy_cloud_regions(deployments, credential_regions)
    proxy.imported_at = datetime.now(UTC)
    return stats


def import_proxy(db: Session, region: DBRegion) -> dict:
    """Read one region's proxy and import it, in one transaction."""
    run = DBRegistryRun(step=STEP, status="running", stats={"region": region.name})
    db.add(run)
    db.commit()
    try:
        with ProxyClient(region.litellm_api_url, region.litellm_api_key) as client:
            deployments = client.model_info()
            version = client.version()
            credential_regions = client.credential_regions()
        stats = import_deployments(db, region, deployments, version, credential_regions)
        run.status, run.stats = "ok", {"region": region.name, **stats}
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return run.stats
