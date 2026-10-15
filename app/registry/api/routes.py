"""Registry endpoints, all system admin only: run stats can hold region names and proxy errors."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import catalog_manages
from app.core.security import get_role_min_system_admin
from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.importer import STEP as IMPORT_STEP
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryCloudAvailability,
    DBRegistryEvent,
    DBRegistryModel,
    DBRegistryModelHistory,
    DBRegistryModelLifecycle,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
    DBRegistryRun,
)
from app.registry.eol import refresh_eol
from app.registry.prices import pick_price, set_manual_prices
from app.registry.schemas import (
    RegistryDeploymentResponse,
    RegistryEventResponse,
    RegistryModelDeploymentResponse,
    RegistryModelDetailResponse,
    RegistryModelProxyResponse,
    RegistryModelResponse,
    RegistryModelUpdate,
    RegistryProviderResponse,
    RegistryProxyDetailResponse,
    RegistryProxyModelResponse,
    RegistryProxyResponse,
    RegistryRunResponse,
)

# The job runs daily, so two days with no `ok` run means a run was missed.
STALE_AFTER = timedelta(hours=48)

router = APIRouter(
    prefix="/registry", tags=["registry"], dependencies=[Depends(get_role_min_system_admin)]
)


def _model_query(db: Session):
    return db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)


def _paginate(query, response: Response, offset: int, limit: int) -> list:
    """The page of an ordered query, with the total before paging in X-Total-Count."""
    response.headers["X-Total-Count"] = str(query.count())
    return query.offset(offset).limit(limit).all()


def _save(db: Session, dry_run: bool) -> None:
    """A dry run makes the same changes, then rolls them back."""
    # The flush runs the database checks, so a dry run fails where the write would.
    db.flush()
    if dry_run:
        db.rollback()
    else:
        db.commit()


def _proxy_results(
    db: Session, model_ids: list[int] | None = None, region_id: int | None = None
) -> dict[int, list[RegistryModelProxyResponse]]:
    """Each model's result per proxy, from its support rows and its deployments."""
    support = db.query(DBRegistryModelSupport, DBRegion.name).join(
        DBRegion, DBRegion.id == DBRegistryModelSupport.region_id
    )
    deployments = db.query(
        DBRegistryModelRegion.model_id, DBRegistryModelRegion.region_id, DBRegistryModelRegion.enabled, DBRegion.name
    ).join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
    if model_ids is not None:
        support = support.filter(DBRegistryModelSupport.model_id.in_(model_ids))
        deployments = deployments.filter(DBRegistryModelRegion.model_id.in_(model_ids))
    if region_id is not None:
        support = support.filter(DBRegistryModelSupport.region_id == region_id)
        deployments = deployments.filter(DBRegistryModelRegion.region_id == region_id)

    merged: dict[tuple[int, int], RegistryModelProxyResponse] = {}
    for row, proxy in support:
        merged[(row.model_id, row.region_id)] = RegistryModelProxyResponse(
            proxy=proxy,
            priced=row.priced,
            supported=row.supported,
            region_available=row.region_available,
            checked_at=row.checked_at,
        )
    for model_id, region, enabled, proxy in deployments:
        entry = merged.setdefault((model_id, region), RegistryModelProxyResponse(proxy=proxy))
        # One enabled deployment is enough for the model to be enabled on the proxy.
        entry.enabled = bool(entry.enabled) or enabled

    results: dict[int, list[RegistryModelProxyResponse]] = defaultdict(list)
    for (model_id, _), entry in sorted(merged.items(), key=lambda item: item[1].proxy):
        results[model_id].append(entry)
    return results


def _model_responses(db: Session, rows: list) -> list[RegistryModelResponse]:
    """The response of each (model, provider name) row, with two batched queries for the extras."""
    model_ids = [model.id for model, _ in rows]
    prices = defaultdict(list)
    proxies = {}
    if rows:
        for price in (
            db.query(DBRegistryModelPrice)
            .filter(DBRegistryModelPrice.model_id.in_(model_ids))
            .order_by(DBRegistryModelPrice.scope_kind, DBRegistryModelPrice.scope)
        ):
            prices[price.model_id].append(price)
        proxies = _proxy_results(db, model_ids=model_ids)

    return [
        RegistryModelResponse(
            provider=provider_name,
            model_id=model.model_id,
            mode=model.mode,
            max_input_tokens=model.max_input_tokens,
            max_output_tokens=model.max_output_tokens,
            input_cost_per_token=model.input_cost_per_token,
            output_cost_per_token=model.output_cost_per_token,
            prices=model.prices,
            eol_date=model.eol_date,
            override_eol=model.override_eol,
            blocked=model.blocked,
            status=model.status,
            family=model.family,
            manufacturer=model.manufacturer,
            source=model.source,
            first_seen=model.first_seen,
            last_seen=model.last_seen,
            price_scopes=prices[model.id],
            proxies=proxies.get(model.id, []),
        )
        for model, provider_name in rows
    ]


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


def _deployment_fields(deployment: DBRegistryModelRegion, proxy: str, groups: dict[int, list[str]]) -> dict:
    return {
        "proxy": proxy,
        "model_name": deployment.model_name,
        "litellm_model": deployment.litellm_model,
        "litellm_deployment_id": deployment.litellm_deployment_id,
        "enabled": deployment.enabled,
        "input_cost_per_token": deployment.input_cost_per_token,
        "output_cost_per_token": deployment.output_cost_per_token,
        "prices": deployment.prices,
        "access_groups": groups.get(deployment.id, []),
    }


def _proxy_responses(db: Session, region_id: int | None = None) -> list[RegistryProxyResponse]:
    """Each region with a registry_proxies row, with its providers and model counts."""
    rows = db.query(DBRegistryProxy, DBRegion).join(DBRegion, DBRegion.id == DBRegistryProxy.region_id)
    providers = (
        db.query(DBRegistryModelRegion.region_id, DBRegistryProvider.name)
        .join(DBRegistryModel, DBRegistryModel.id == DBRegistryModelRegion.model_id)
        .join(DBRegistryProvider)
        .distinct()
    )
    supported = db.query(DBRegistryModelSupport.region_id, func.count()).filter(
        DBRegistryModelSupport.supported.is_(True)
    )
    enabled = db.query(
        DBRegistryModelRegion.region_id, func.count(DBRegistryModelRegion.model_id.distinct())
    ).filter(DBRegistryModelRegion.enabled.is_(True))
    if region_id is not None:
        rows = rows.filter(DBRegistryProxy.region_id == region_id)
        providers = providers.filter(DBRegistryModelRegion.region_id == region_id)
        supported = supported.filter(DBRegistryModelSupport.region_id == region_id)
        enabled = enabled.filter(DBRegistryModelRegion.region_id == region_id)

    provider_names: dict[int, list[str]] = defaultdict(list)
    for region, name in providers.order_by(DBRegistryProvider.name):
        provider_names[region].append(name)
    supported_counts = dict(supported.group_by(DBRegistryModelSupport.region_id))
    enabled_counts = dict(enabled.group_by(DBRegistryModelRegion.region_id))

    return [
        RegistryProxyResponse(
            proxy=region.name,
            region_id=region.id,
            litellm_version=proxy.litellm_version,
            cloud_regions=proxy.cloud_regions,
            imported_at=proxy.imported_at,
            providers=provider_names[region.id],
            owner="catalog" if catalog_manages(region.name) else "none",
            models_supported=supported_counts.get(region.id, 0),
            models_enabled=enabled_counts.get(region.id, 0),
        )
        for proxy, region in rows.order_by(DBRegion.name)
    ]


@router.get("/runs", response_model=list[RegistryRunResponse])
def list_runs(
    response: Response,
    limit: int = Query(50, ge=1, le=500),
    step: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(DBRegistryRun)
    if step:
        query = query.filter(DBRegistryRun.step == step)
    fresh = {
        name
        for (name,) in db.query(DBRegistryRun.step)
        .filter(DBRegistryRun.status == "ok", DBRegistryRun.started_at >= datetime.now(UTC) - STALE_AFTER)
        .distinct()
    }
    # The import runs one time per proxy, not daily, so an old import is never a missed run.
    fresh.add(IMPORT_STEP)
    # A run row is inserted when its step starts, so the id order is the start order.
    return [
        RegistryRunResponse.model_validate(run).model_copy(update={"stale": run.step not in fresh})
        for run in _paginate(query.order_by(DBRegistryRun.id.desc()), response, 0, limit)
    ]


@router.get("/models", response_model=list[RegistryModelResponse])
def list_models(
    response: Response,
    provider: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    family: str | None = Query(None),
    proxy: str | None = Query(None),
    enabled: bool | None = Query(None),
    supported: bool | None = Query(None),
    priced: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = _model_query(db)
    region_ids = select(DBRegion.id).where(DBRegion.name == proxy)
    if provider:
        query = query.filter(DBRegistryProvider.name == provider)
    if status:
        query = query.filter(DBRegistryModel.status == status)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(DBRegistryModel.model_id.ilike(f"%{escaped}%", escape="\\"))
    if family:
        query = query.filter(DBRegistryModel.family == family)
    if proxy:
        deployed = select(DBRegistryModelRegion.model_id).where(DBRegistryModelRegion.region_id.in_(region_ids))
        query = query.filter(DBRegistryModel.id.in_(deployed))
    if enabled is not None:
        on = select(DBRegistryModelRegion.model_id).where(DBRegistryModelRegion.enabled.is_(True))
        if proxy:
            on = on.where(DBRegistryModelRegion.region_id.in_(region_ids))
        query = query.filter(DBRegistryModel.id.in_(on) if enabled else DBRegistryModel.id.not_in(on))
    if supported is not None:
        # False means a proxy said no; an unknown (null) result matches neither value.
        matches = select(DBRegistryModelSupport.model_id).where(DBRegistryModelSupport.supported.is_(supported))
        if proxy:
            matches = matches.where(DBRegistryModelSupport.region_id.in_(region_ids))
        query = query.filter(DBRegistryModel.id.in_(matches))
    if priced is not None:
        has_price = or_(
            DBRegistryModel.input_cost_per_token.is_not(None),
            DBRegistryModel.output_cost_per_token.is_not(None),
            DBRegistryModel.id.in_(select(DBRegistryModelPrice.model_id)),
        )
        query = query.filter(has_price if priced else ~has_price)
    query = query.order_by(DBRegistryProvider.name, DBRegistryModel.model_id)
    return _model_responses(db, _paginate(query, response, offset, limit))


@router.get("/models/new", response_model=list[RegistryModelResponse])
def new_models(
    response: Response,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    # The daily job sets first_seen from date.today(), so the window uses the same clock.
    query = (
        _model_query(db)
        .filter(DBRegistryModel.first_seen >= date.today() - timedelta(days=days))
        .order_by(DBRegistryModel.first_seen.desc(), DBRegistryProvider.name, DBRegistryModel.model_id)
    )
    return _model_responses(db, _paginate(query, response, offset, limit))


@router.get("/models/eol", response_model=list[RegistryModelResponse])
def eol_models(
    response: Response,
    days: int = Query(90, ge=1, le=365),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    today = date.today()
    query = (
        _model_query(db)
        .filter(
            or_(
                DBRegistryModel.eol_date.between(today - timedelta(days=7), today + timedelta(days=days)),
                # A retirement with no date yet still needs a replacement.
                and_(DBRegistryModel.status == "deprecated", DBRegistryModel.eol_date.is_(None)),
            )
        )
        .order_by(DBRegistryModel.eol_date.asc().nulls_last(), DBRegistryProvider.name, DBRegistryModel.model_id)
    )
    return _model_responses(db, _paginate(query, response, offset, limit))


def _find_model(db: Session, key: str) -> tuple[DBRegistryModel, str]:
    """The model of a `provider/model_id` key, with its provider name."""
    # Only the first slash splits: model ids can hold slashes (deepinfra/BAAI/bge-m3).
    provider, _, model_id = key.partition("/")
    row = (
        _model_query(db)
        .filter(DBRegistryProvider.name == provider, DBRegistryModel.model_id == model_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return row


def _find_proxy(db: Session, proxy: str) -> DBRegion:
    """The region of a proxy name; only a region with a registry_proxies row is a proxy."""
    region = (
        db.query(DBRegion)
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .filter(DBRegion.name == proxy)
        .one_or_none()
    )
    if region is None:
        raise HTTPException(status_code=404, detail="Proxy not found")
    return region


def _deployment_responses(
    db: Session, price_scopes: list, provider: str, deployments: list[tuple[DBRegistryModelRegion, str]]
) -> list[RegistryModelDeploymentResponse]:
    """The response of each (deployment, proxy name) of one model, with the price the registry writes."""
    cloud_regions = dict(
        db.query(DBRegistryProxy.region_id, DBRegistryProxy.cloud_regions).filter(
            DBRegistryProxy.region_id.in_({deployment.region_id for deployment, _ in deployments})
        )
    )
    groups = _access_groups(db, [deployment.id for deployment, _ in deployments])
    # pick_price gets the deployed model string, so a geo id gets its geo price.
    return [
        RegistryModelDeploymentResponse(
            **_deployment_fields(deployment, proxy, groups),
            price=pick_price(
                price_scopes, provider, deployment.litellm_model,
                (cloud_regions.get(deployment.region_id) or {}).get(provider),
            ),
        )
        for deployment, proxy in deployments
    ]


def _model_detail(db: Session, model: DBRegistryModel, provider: str) -> RegistryModelDetailResponse:
    base = _model_responses(db, [(model, provider)])[0]

    lifecycle = (
        db.query(DBRegistryModelLifecycle)
        .filter_by(provider=provider, model_id=model.model_id)
        .order_by(DBRegistryModelLifecycle.source)
        .all()
    )
    availability = (
        db.query(DBRegistryCloudAvailability)
        .filter_by(provider=provider, model_id=model.model_id)
        .order_by(DBRegistryCloudAvailability.cloud_region, DBRegistryCloudAvailability.source)
        .all()
    )
    deployments = (
        db.query(DBRegistryModelRegion, DBRegion.name)
        .join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
        .filter(DBRegistryModelRegion.model_id == model.id)
        .order_by(DBRegion.name, DBRegistryModelRegion.model_name)
        .all()
    )
    history = (
        db.query(DBRegistryModelHistory)
        .filter(DBRegistryModelHistory.model_id == model.id)
        .order_by(DBRegistryModelHistory.recorded_at.desc(), DBRegistryModelHistory.id.desc())
        .all()
    )

    return RegistryModelDetailResponse(
        **base.model_dump(),
        supports=model.supports,
        lifecycle=lifecycle,
        availability=availability,
        deployments=_deployment_responses(db, base.price_scopes, provider, deployments),
        history=history,
    )


# Declared after /models/new and /models/eol: a path parameter would catch those two names.
@router.get("/models/{key:path}", response_model=RegistryModelDetailResponse)
def get_model(key: str, db: Session = Depends(get_db)):
    model, provider = _find_model(db, key)
    return _model_detail(db, model, provider)


@router.patch("/models/{key:path}", response_model=RegistryModelDetailResponse)
def update_model(
    key: str,
    body: RegistryModelUpdate,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    model, provider = _find_model(db, key)
    changes = body.model_dump(exclude_unset=True)
    for field in ("family", "override_eol", "blocked"):
        if field in changes and getattr(model, field) != changes[field]:
            setattr(model, field, changes[field])
            model.updated_at = datetime.now(UTC)
    if "prices" in changes:
        # ponytail: a daily step that runs at the same time can overwrite a manual price
        # written here; the admin sets it again. A row lock in this route would close that.
        set_manual_prices(db, model, changes["prices"], date.today())
    if "override_eol" in changes:
        refresh_eol(db, model, provider)
    db.flush()
    detail = _model_detail(db, model, provider)
    _save(db, dry_run)
    return detail


@router.get("/providers", response_model=list[RegistryProviderResponse])
def list_providers(response: Response, db: Session = Depends(get_db)):
    providers = db.query(DBRegistryProvider).order_by(DBRegistryProvider.name).all()
    model_counts = dict(
        db.query(DBRegistryModel.provider_id, func.count(DBRegistryModel.id)).group_by(DBRegistryModel.provider_id)
    )
    proxies: dict[int, set[str]] = defaultdict(set)
    deployed: dict[int, set[int]] = defaultdict(set)
    for provider_id, proxy, model_id in (
        db.query(DBRegistryModel.provider_id, DBRegion.name, DBRegistryModel.id)
        .join(DBRegistryModelRegion, DBRegistryModelRegion.model_id == DBRegistryModel.id)
        .join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
        .distinct()
    ):
        proxies[provider_id].add(proxy)
        deployed[provider_id].add(model_id)

    response.headers["X-Total-Count"] = str(len(providers))
    return [
        RegistryProviderResponse(
            provider=provider.name,
            proxies=sorted(proxies[provider.id]),
            models=model_counts.get(provider.id, 0),
            deployed_models=len(deployed[provider.id]),
        )
        for provider in providers
    ]


@router.get("/proxies", response_model=list[RegistryProxyResponse])
def list_proxies(response: Response, db: Session = Depends(get_db)):
    proxies = _proxy_responses(db)
    response.headers["X-Total-Count"] = str(len(proxies))
    return proxies


@router.get("/proxies/{proxy}", response_model=RegistryProxyDetailResponse)
def get_proxy(proxy: str, db: Session = Depends(get_db)):
    region = _find_proxy(db, proxy)
    found = _proxy_responses(db, region.id)

    checked_at = (
        db.query(func.max(DBRegistryModelSupport.checked_at))
        .filter(DBRegistryModelSupport.region_id == region.id)
        .scalar()
    )
    # The models with a support row or a deployment on this proxy.
    support = _proxy_results(db, region_id=region.id)
    rows = (
        _model_query(db)
        .filter(DBRegistryModel.id.in_(support.keys()))
        .order_by(DBRegistryProvider.name, DBRegistryModel.model_id)
        .all()
    )
    deployments = (
        db.query(DBRegistryModelRegion)
        .filter(DBRegistryModelRegion.region_id == region.id)
        .order_by(DBRegistryModelRegion.model_name)
        .all()
    )
    groups = _access_groups(db, [deployment.id for deployment in deployments])
    by_model: dict[int, list[RegistryDeploymentResponse]] = defaultdict(list)
    for deployment in deployments:
        by_model[deployment.model_id].append(
            RegistryDeploymentResponse(**_deployment_fields(deployment, region.name, groups))
        )

    return RegistryProxyDetailResponse(
        **found[0].model_dump(),
        checked_at=checked_at,
        models=[
            RegistryProxyModelResponse(
                key=f"{provider}/{model.model_id}",
                status=model.status,
                eol_date=model.eol_date,
                support=support[model.id][0],
                deployments=by_model[model.id],
            )
            for model, provider in rows
        ],
    )


@router.get("/events", response_model=list[RegistryEventResponse])
def list_events(
    response: Response,
    event_type: str | None = Query(None, alias="type"),
    since: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    # Most events are about a model, not a proxy, so the region join is an outer join.
    query = (
        db.query(DBRegistryEvent, DBRegistryProvider.name, DBRegistryModel.model_id, DBRegion.name)
        .join(DBRegistryModel, DBRegistryModel.id == DBRegistryEvent.model_id)
        .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
        .outerjoin(DBRegion, DBRegion.id == DBRegistryEvent.region_id)
    )
    if event_type:
        query = query.filter(DBRegistryEvent.type == event_type)
    if since:
        query = query.filter(DBRegistryEvent.created_at >= since)
    # The events of one run share now(), so the id keeps their order.
    query = query.order_by(DBRegistryEvent.created_at.desc(), DBRegistryEvent.id.desc())
    return [
        RegistryEventResponse(
            key=f"{provider}/{model_id}", proxy=proxy, type=event.type, created_at=event.created_at, data=event.data
        )
        for event, provider, model_id, proxy in _paginate(query, response, offset, limit)
    ]
