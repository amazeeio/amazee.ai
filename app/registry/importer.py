"""Import of a proxy's live deployments into the registry.

The API adds planned deployments to a proxy only after this import. A second
plain import is refused: it would overwrite registry decisions with proxy state.
The adopt mode only adds what the registry does not have yet, so it can run again.
"""

import logging
from collections.abc import Set as AbstractSet
from datetime import UTC, date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry.discovery import entry_fields
from app.registry.litellm import (
    ProxyClient,
    is_env_ref,
    mantle_region,
    registry_provider_and_model,
    split_model,
    without_alias_copies,
    without_secrets,
)
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelPrice,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.planner import alias_entry
from app.registry.runs import record_run

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
    # The EOL step is the only writer of eol_date; a date here would be cleared on its next run.
    del fields["eol_date"]
    # /model/info fills prices with 0 for a model LiteLLM does not know.
    # Stored as 0 it would look free, so it is stored as unknown.
    if not any(fields["prices"].values()):
        fields["input_cost_per_token"] = fields["output_cost_per_token"] = None
        fields["prices"] = {}
    return fields


def _literal(value):
    # An os.environ/ value names a variable on the proxy host, not a region.
    return None if is_env_ref(value) else value


def _proxy_cloud_regions(deployments: list[dict], credential_regions: dict[str, str]) -> dict:
    """The cloud region each provider on the proxy calls, first one seen wins."""
    regions: dict[str, str] = {}
    for dep in deployments:
        params = dep.get("litellm_params") or {}
        provider = registry_provider_and_model(params, dep.get("model_info"))[0]
        if not provider or provider in regions:
            continue
        if provider.startswith("bedrock"):
            region = _literal(params.get("aws_region_name")) or _literal(
                credential_regions.get(params.get("litellm_credential_name"))
            ) or mantle_region(params.get("api_base"))
        elif provider == "vertex_ai":
            region = _literal(params.get("vertex_location"))
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
    aliases: AbstractSet[str] = frozenset(),
    today: date | None = None,
    adopt: bool = False,
) -> dict:
    """Add the live deployments. With `adopt`, keep every deployment the registry already holds."""
    proxy = db.get(DBRegistryProxy, region.id)
    if not adopt and proxy is not None and proxy.imported_at is not None:
        raise AlreadyImported(f"region {region.name} was imported at {proxy.imported_at}")
    today = today or date.today()

    kept, copies = without_alias_copies(deployments, aliases)
    stats = {"deployments": len(deployments), "imported": 0, "skipped": [], "aliases": copies}
    known = set()
    if adopt:
        stats["kept"] = 0
        known = {
            dep_id
            for (dep_id,) in db.query(DBRegistryModelRegion.litellm_deployment_id).filter(
                DBRegistryModelRegion.region_id == region.id, DBRegistryModelRegion.litellm_deployment_id.isnot(None)
            )
        }
    for dep in kept:
        params = dep.get("litellm_params") or {}
        info = dep.get("model_info") or {}
        if info.get("id") in known:
            stats["kept"] += 1
            continue
        litellm_model = params.get("model") or ""
        provider_name, source_model = registry_provider_and_model(params, info)
        split = split_model(source_model, provider_name) if provider_name else None
        if split is None:
            # No provider, not even from LiteLLM, or a model string we do not track (one region, commitment).
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
        # A model only the proxy knows starts with the proxy's price. The
        # daily job keeps it until LiteLLM's list prices the model itself.
        if model.source == "proxy" and model.prices and db.get(DBRegistryModelPrice, (model.id, "base", "")) is None:
            db.add(
                DBRegistryModelPrice(
                    model_id=model.id, scope_kind="base", scope="", prices=model.prices,
                    source="proxy", last_seen=today,
                )
            )
            db.flush()
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
            litellm_params=without_secrets(params),
            model_info=without_secrets(info),
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
    found = _proxy_cloud_regions(deployments, credential_regions)
    # An admin can set a region, so a stored value wins and the adopt mode only adds new providers.
    proxy.cloud_regions = {**found, **(proxy.cloud_regions or {})} if adopt else found
    if proxy.imported_at is None:
        proxy.imported_at = datetime.now(UTC)
    return stats


def adopt_aliases(db: Session, region: DBRegion, alias_map: dict) -> int:
    """Add each live alias that the registry does not have, with its target and hidden flag. Rows stay."""
    added = 0
    for alias, value in alias_map.items():
        entry = alias_entry(value)
        if not isinstance(alias, str) or entry is None or not isinstance(entry[0], str):
            continue
        result = db.execute(
            insert(DBRegistryAlias)
            .values(region_id=region.id, alias=alias, target=entry[0], hidden=entry[1])
            .on_conflict_do_nothing(index_elements=["region_id", "alias"])
        )
        added += result.rowcount
    return added


def adopt_credentials(db: Session, region: DBRegion, infos: dict[str, dict]) -> int:
    """Add a credential row with no values and no provider for each live credential name, and link the proxy.

    The sync never pushes a credential with no values. Rule 6 takes its provider from the deployments that use it.
    """
    # ponytail: one row per credential name; a name with other values on another proxy shares the row.
    added = 0
    for name, info in sorted(infos.items()):
        # ON CONFLICT, so two imports at the same time cannot both insert one name.
        result = db.execute(
            insert(DBRegistryCredential)
            .values(name=name, provider=None, values_encrypted=None, credential_info=info)
            .on_conflict_do_nothing(index_elements=["name"])
        )
        added += result.rowcount
        credential_id = db.query(DBRegistryCredential.id).filter_by(name=name).scalar()
        db.execute(
            insert(DBRegistryCredentialRegion)
            .values(credential_id=credential_id, region_id=region.id)
            .on_conflict_do_nothing()
        )
    return added


def import_proxy(db: Session, region: DBRegion, adopt: bool = False) -> dict:
    """Read one region's proxy and import it, in one transaction."""
    extra = {"region": region.name, **({"adopt": True} if adopt else {})}
    return record_run(db, STEP, lambda: _import(db, region, adopt), extra=extra)


def _import(db: Session, region: DBRegion, adopt: bool = False) -> dict:
    # Every read comes first, so no lock is held during HTTP.
    with ProxyClient(region.litellm_api_url, region.litellm_api_key) as client:
        deployments = client.model_info()
        version = client.version()
        credential_regions = client.credential_regions()
        # No fallback on error: `/model/info` lists each alias again, with the target's id.
        alias_map = client.alias_map()
        infos = client.credential_infos() if adopt else {}
    if not adopt:
        return import_deployments(db, region, deployments, version, credential_regions, aliases=set(alias_map))
    # Two adopt imports of one proxy run one after the other, so each deployment is added one time.
    db.query(DBRegistryProxy).filter_by(region_id=region.id).populate_existing().with_for_update().one_or_none()
    stats = import_deployments(
        db, region, deployments, version, credential_regions, aliases=set(alias_map), adopt=True
    )
    stats["aliases_added"] = adopt_aliases(db, region, alias_map)
    stats["credentials_added"] = adopt_credentials(db, region, infos)
    return stats
