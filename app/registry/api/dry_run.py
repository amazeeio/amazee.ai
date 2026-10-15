"""The end of every registry write: save it, or in a dry run, roll it back and plan the proxy steps.

Not imported from routes.py, so the route modules can import it with no cycle.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from fastapi import Response
from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry import planner
from app.registry.litellm import ProxyClient
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.prices import pick_price
from app.registry.schemas import RegistryDryRun
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


def _save(
    db: Session,
    dry_run: bool,
    result=None,
    *,
    response: Response | None = None,
    plan: Callable[[], list[ProxyPlan]] | None = None,
):
    """Commit the write and return `result`, or in a dry run, roll it back and return it with the planned steps.

    `plan` runs only in a dry run, after the flush, so a write that is not a dry run never reads a proxy.
    """
    # The flush runs the database checks, so a dry run fails where the write would.
    db.flush()
    if not dry_run:
        db.commit()
        return result
    plans = plan() if plan else []
    # End the transaction before the proxy reads, so no row lock is held during HTTP.
    db.rollback()
    if response is not None:
        response.status_code = 200
    return RegistryDryRun(result=None if isinstance(result, Response) else result, steps=plan_steps(plans))


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
    regions = {
        region_id: (name, url, key)
        for region_id, name, url, key in db.query(
            DBRegion.id, DBRegion.name, DBRegion.litellm_api_url, DBRegion.litellm_api_key
        ).filter(DBRegion.id.in_(region_ids))
    }
    models = {
        model_id: (provider, name)
        for model_id, provider, name in db.query(DBRegistryModel.id, DBRegistryProvider.name, DBRegistryModel.model_id)
        .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
        .filter(DBRegistryModel.id.in_(model_ids))
    }
    # A delete already removed the links of its rows.
    groups = {} if removed else _access_groups(db, [row.id for row in rows])
    prices = "prices" in fields
    if prices:
        price_rows = defaultdict(list)
        for price in db.query(DBRegistryModelPrice).filter(DBRegistryModelPrice.model_id.in_(model_ids)):
            price_rows[price.model_id].append(price)
        cloud_regions = dict(
            db.query(DBRegistryProxy.region_id, DBRegistryProxy.cloud_regions).filter(
                DBRegistryProxy.region_id.in_(region_ids)
            )
        )
        availability = Availability(db)

    by_region: dict[int, list[planner.Deployment]] = defaultdict(list)
    for row in rows:
        provider, model_id = models[row.model_id]
        price, provisioned_only = None, False
        if prices:
            cloud_region = (cloud_regions.get(row.region_id) or {}).get(provider)
            picked = pick_price(price_rows[row.model_id], provider, row.litellm_model, cloud_region)
            price = dict(picked.prices) if picked else None
            # The same detection as the support check's `deployed_provisioned_only`.
            provisioned_only = (
                availability.in_region_problem(provider, model_id, cloud_region, row.litellm_model)
                == "provisioned_only"
            )
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
        ))
    return [ProxyPlan(*regions[region_id], deployments, fields) for region_id, deployments in by_region.items()]


def alias_plans(db: Session, region: DBRegion) -> list[ProxyPlan]:
    """The full alias map of the proxy, in the LiteLLM form, because the sync always sends the full map."""
    aliases = {
        row.alias: {"model": row.target, "hidden": True} if row.hidden else row.target
        for row in db.query(DBRegistryAlias).filter(DBRegistryAlias.region_id == region.id)
    }
    return [ProxyPlan(region.name, region.litellm_api_url, region.litellm_api_key, aliases=aliases)]


def read_live(url: str, key: str) -> planner.LiveProxy | str:
    """The live state of a proxy, or a short reason why it could not be read. The reason never holds the URL or key."""
    try:
        with ProxyClient(url, key) as client:
            rows = client.model_info()
            aliases = client.alias_map()
            credentials = client.credential_names()
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPStatusError as exc:
        return f"HTTP {exc.response.status_code}"
    # InvalidURL is not an HTTPError: a malformed litellm_api_url raises it before any request.
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return type(exc).__name__
    except (ValueError, AttributeError, TypeError):
        return "bad JSON"
    if not isinstance(rows, list):
        return "bad JSON"
    return planner.LiveProxy([row for row in rows if isinstance(row, dict)], aliases, frozenset(credentials))


def plan_steps(plans: list[ProxyPlan]) -> list[dict]:
    """The planned steps of each proxy, sorted by proxy name. A proxy that cannot be read gives one step."""
    steps = []
    # ponytail: the reads run one after the other, so each proxy that does not answer adds up to
    # HTTP_TIMEOUT_SECONDS. Read the proxies at the same time if the dry runs become too slow.
    for plan in sorted(plans, key=lambda plan: plan.proxy):
        live = read_live(plan.url, plan.key)
        if isinstance(live, str):
            steps.append(
                {"proxy": plan.proxy, "action": "read_proxy", "problems": [f"the proxy could not be read: {live}"]}
            )
        else:
            steps += planner.plan(plan.proxy, live, plan.deployments, plan.fields, plan.aliases)
    return steps
