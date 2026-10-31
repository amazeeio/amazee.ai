"""The planning helpers of the dry run and the sync: the wanted state of each proxy, and its planned steps.

The sync module imports this one, and the route modules import both, so no import goes the other way.
"""

from collections import defaultdict
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry import planner
from app.registry.litellm import ProxyClient
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.prices import pick_price
from app.registry.rules import credential_provider, user_providers
from app.registry.support import Availability


@dataclass
class ProxyPlan:
    """The wanted state of one proxy, in plain data, so it outlives the rollback."""

    proxy: str
    url: str
    # repr=False: the master key never shows in a repr, a log or a traceback.
    key: str = field(repr=False)
    deployments: list[planner.Deployment] = field(default_factory=list)
    fields: frozenset = planner.DEPLOYMENT_FIELDS
    aliases: dict | None = None
    removed_alias: str | None = None
    active: bool = True
    # The LiteLLM deployment ids that the registry holds on the proxy.
    known_ids: frozenset = frozenset()
    credentials: tuple = ()
    # The providers of the registry credentials on the proxy, mapped for rule 6.
    credential_providers: frozenset = frozenset()
    region_id: int | None = None
    # False until the adopt import read the proxy's live aliases.
    aliases_adopted: bool = True


def _access_groups(db: Session, deployment_ids: list[int]) -> dict[int, list[str]]:
    """The sorted access group slugs of each deployment."""
    groups: dict[int, list[str]] = defaultdict(list)
    if deployment_ids:
        rows = (
            db.query(DBRegistryModelRegionGroup.model_region_id, DBRegistryAccessGroup.slug)
            .join(DBRegistryAccessGroup, DBRegistryAccessGroup.id == DBRegistryModelRegionGroup.access_group_id)
            .filter(DBRegistryModelRegionGroup.model_region_id.in_(deployment_ids))
            .order_by(DBRegistryAccessGroup.slug)
        )
        for deployment_id, slug in rows:
            groups[deployment_id].append(slug)
    return groups


def _proxy_plan(region: DBRegion, **wanted) -> ProxyPlan:
    return ProxyPlan(
        region.name, region.litellm_api_url, region.litellm_api_key, active=region.is_active is True,
        region_id=region.id, **wanted,
    )


def deployment_plans(
    db: Session, rows: list[DBRegistryModelRegion], fields: frozenset, *, removed: bool = False,
    live_only: bool = False,
) -> list[ProxyPlan]:
    """One plan for each proxy of the deployment rows, in a fixed number of queries.

    It reads only the attributes of the rows, so rows that a bulk delete removed still work.
    """
    if live_only:
        rows = [row for row in rows if row.enabled and row.litellm_deployment_id]
    if not rows:
        return []
    region_ids = {row.region_id for row in rows}
    model_ids = {row.model_id for row in rows}
    regions = {region.id: region for region in db.query(DBRegion).filter(DBRegion.id.in_(region_ids))}
    models = {
        model_id: (provider, name)
        for model_id, provider, name in db.query(DBRegistryModel.id, DBRegistryProvider.name, DBRegistryModel.model_id)
        .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
        .filter(DBRegistryModel.id.in_(model_ids))
    }
    # A delete already removed the links of its rows.
    groups = {} if removed else _access_groups(db, [row.id for row in rows])
    # Every row gets its price, because a create writes it whatever the write changed.
    price_rows = defaultdict(list)
    for price in db.query(DBRegistryModelPrice).filter(DBRegistryModelPrice.model_id.in_(model_ids)):
        price_rows[price.model_id].append(price)
    cloud_regions = dict(
        db.query(DBRegistryProxy.region_id, DBRegistryProxy.cloud_regions).filter(
            DBRegistryProxy.region_id.in_(region_ids)
        )
    )
    availability = Availability(db)
    known_ids = _known_ids(db, region_ids)
    providers = credential_providers(db, region_ids)

    by_region: dict[int, list[planner.Deployment]] = defaultdict(list)
    for row in rows:
        provider, model_id = models[row.model_id]
        cloud_region = (cloud_regions.get(row.region_id) or {}).get(provider)
        picked = pick_price(price_rows[row.model_id], provider, row.litellm_model, cloud_region)
        price = dict(picked.prices) if picked else None
        # The same detection as the support check's `deployed_provisioned_only`.
        provisioned_only = (
            availability.in_region_problem(provider, model_id, cloud_region, row.litellm_model)
            == "provisioned_only"
        )
        info = row.model_info or {}
        by_region[row.region_id].append(planner.Deployment(
            model=f"{provider}/{model_id}",
            model_name=row.model_name,
            litellm_model=row.litellm_model,
            credential=(row.litellm_params or {}).get("litellm_credential_name"),
            access_groups=tuple(groups.get(row.id, ())),
            deployment_id=row.litellm_deployment_id,
            keep=row.enabled and not removed,
            price=price,
            provisioned_only=provisioned_only,
            row_id=None if removed else row.id,
            # An adopted row has the model_info of the proxy and no marker; an API row has none until the sync.
            recreate=not info or info.get("managed_by") == planner.MANAGED_BY,
            params=dict(row.litellm_params or {}),
        ))
    return [
        _proxy_plan(
            regions[region_id], deployments=deployments, fields=fields,
            known_ids=known_ids.get(region_id, frozenset()), credential_providers=providers.get(region_id, frozenset()),
        )
        for region_id, deployments in by_region.items()
    ]


def credential_providers(db: Session, region_ids) -> dict[int, frozenset]:
    """The mapped providers of the registry credentials on each proxy. A credential with no provider counts for
    the providers of the deployments that use it, as in rule 6."""
    links = (
        db.query(DBRegistryCredentialRegion.region_id, DBRegistryCredential.name, DBRegistryCredential.provider)
        .join(DBRegistryCredential, DBRegistryCredential.id == DBRegistryCredentialRegion.credential_id)
        .filter(DBRegistryCredentialRegion.region_id.in_(region_ids))
        .all()
    )
    users = user_providers(db, {name for _, name, provider in links if not provider})
    found = defaultdict(set)
    for region_id, name, provider in links:
        found[region_id] |= {credential_provider(provider)} if provider else users.get(name, set())
    return {region_id: frozenset(providers) for region_id, providers in found.items()}


def proxy_credentials(db: Session, region_id: int) -> list[str]:
    """The sorted names of the registry credentials on one proxy."""
    return [
        name
        for (name,) in db.query(DBRegistryCredential.name)
        .join(DBRegistryCredentialRegion, DBRegistryCredentialRegion.credential_id == DBRegistryCredential.id)
        .filter(DBRegistryCredentialRegion.region_id == region_id)
        .order_by(DBRegistryCredential.name)
    ]


def credential_plans(db: Session, name: str, region_ids, rotate: bool = False, keep: bool = True) -> list[ProxyPlan]:
    """One plan per proxy for one credential. A deleted credential plans only its delete, so it needs no row."""
    row = db.query(DBRegistryCredential).filter_by(name=name).one_or_none()
    credential = planner.Credential(
        name=name,
        info=dict(row.credential_info or {}) if row else {},
        has_values=bool(row and row.values_encrypted),
        rotate=rotate,
        keep=keep,
    )
    regions = db.query(DBRegion).filter(DBRegion.id.in_(region_ids)).order_by(DBRegion.id)
    return [_proxy_plan(region, credentials=(credential,)) for region in regions]


def _known_ids(db: Session, region_ids) -> dict[int, frozenset]:
    """The LiteLLM deployment ids that the registry holds, per region, in one query."""
    ids = defaultdict(set)
    for region_id, deployment_id in db.query(
        DBRegistryModelRegion.region_id, DBRegistryModelRegion.litellm_deployment_id
    ).filter(DBRegistryModelRegion.region_id.in_(region_ids), DBRegistryModelRegion.litellm_deployment_id.isnot(None)):
        ids[region_id].add(deployment_id)
    return {region_id: frozenset(found) for region_id, found in ids.items()}


def alias_plans(db: Session, region: DBRegion, removed_alias: str | None = None) -> list[ProxyPlan]:
    """The full alias map of the proxy, in the LiteLLM form, because the sync always sends the full map."""
    aliases = {
        row.alias: {"model": row.target, "hidden": True} if row.hidden else row.target
        for row in db.query(DBRegistryAlias).filter(DBRegistryAlias.region_id == region.id)
    }
    adopted = db.query(DBRegistryProxy.adopted_at).filter_by(region_id=region.id).scalar() is not None
    return [_proxy_plan(region, aliases=aliases, removed_alias=removed_alias, aliases_adopted=adopted)]


def _readable_row(row: dict) -> dict | None:
    """The row in the types the planner compares, or None when a value has another type.

    A `litellm_params` or `model_info` that is not a dict reads as `{}`: with no id, the row is never an alias
    copy and never matches a deployment. A missing or null `access_groups` reads as `[]`.
    """
    row = {**row, **{name: {} for name in ("litellm_params", "model_info") if not isinstance(row.get(name), dict)}}
    info = row["model_info"]
    groups = info.get("access_groups")
    groups = [] if groups is None else groups
    if not (
        isinstance(row.get("model_name"), str | None)
        and isinstance(info.get("id"), str | None)
        and isinstance(groups, list)
        and all(isinstance(group, str) for group in groups)
    ):
        return None
    row["model_info"] = {**info, "access_groups": groups}
    return row


def short_reason(exc: Exception) -> str:
    """Why a proxy read failed, in a few words. It never holds the URL or the key."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, ValueError | AttributeError | TypeError):
        return "bad JSON"
    return type(exc).__name__


def read_live(url: str, key: str) -> planner.LiveProxy | str:
    """The live state of a proxy, or a short reason why it could not be read. The reason never holds the URL or key."""
    try:
        with ProxyClient(url, key) as client:
            rows = client.model_info()
            aliases = client.alias_map()
            credentials = client.credential_names()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, AttributeError, TypeError) as exc:
        return short_reason(exc)
    if not isinstance(rows, list):
        return "bad JSON"
    rows = [_readable_row(row) for row in rows if isinstance(row, dict)]
    if None in rows:
        return "bad data"
    return planner.LiveProxy(rows, aliases, frozenset(credentials))


def unreadable(url: str | None, active: bool) -> str | None:
    """Why the sync does not read a proxy at all, or None."""
    if not url:
        return "no proxy URL"
    if not active:
        return "the proxy is inactive"
    return None


def plan_steps(plans: list[ProxyPlan]) -> list[dict]:
    """The planned steps of each proxy, sorted by proxy name. A proxy that cannot be read gives one step."""
    steps = []
    # ponytail: the reads run one after the other, so each proxy that does not answer adds up to
    # HTTP_TIMEOUT_SECONDS. Read the proxies at the same time if the dry runs become too slow.
    for plan in sorted(plans, key=lambda plan: plan.proxy):
        live = unreadable(plan.url, plan.active) or read_live(plan.url, plan.key)
        if isinstance(live, str):
            steps.append(
                {
                    "proxy": plan.proxy, "action": "read_proxy", "problems": [f"the proxy could not be read: {live}"],
                    "blocked": True,
                }
            )
        else:
            steps += plan_proxy(plan, live)
    return steps


def plan_proxy(plan: ProxyPlan, live: planner.LiveProxy) -> list[dict]:
    """The planner steps of one proxy plan against its live state."""
    return planner.plan(
        plan.proxy, live, plan.deployments, plan.fields, plan.aliases, plan.removed_alias, plan.known_ids,
        plan.credentials, plan.credential_providers, plan.aliases_adopted,
    )
