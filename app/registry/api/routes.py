"""Registry endpoints, all system admin only: run stats can hold region names and proxy errors."""

from collections import defaultdict

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import get_role_min_system_admin
from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryRun,
)
from app.registry.schemas import RegistryModelProxyResponse, RegistryModelResponse, RegistryRunResponse

router = APIRouter(
    prefix="/registry", tags=["registry"], dependencies=[Depends(get_role_min_system_admin)]
)


def _model_query(db: Session):
    return db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)


def _paginate(query, response: Response, offset: int, limit: int) -> list:
    """The page of an ordered query, with the total before paging in X-Total-Count."""
    response.headers["X-Total-Count"] = str(query.count())
    return query.offset(offset).limit(limit).all()


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

    merged: dict[tuple[int, int], dict] = {}
    for row, proxy in support:
        merged[(row.model_id, row.region_id)] = {
            "proxy": proxy,
            "priced": row.priced,
            "supported": row.supported,
            "region_available": row.region_available,
            "checked_at": row.checked_at,
            "enabled": None,
        }
    empty = dict.fromkeys(("priced", "supported", "region_available", "checked_at", "enabled"))
    for model_id, region, enabled, proxy in deployments:
        entry = merged.setdefault((model_id, region), {**empty, "proxy": proxy})
        # One enabled deployment is enough for the model to be enabled on the proxy.
        entry["enabled"] = bool(entry["enabled"]) or enabled

    results: dict[int, list[RegistryModelProxyResponse]] = defaultdict(list)
    for (model_id, _), entry in sorted(merged.items(), key=lambda item: item[1]["proxy"]):
        results[model_id].append(RegistryModelProxyResponse(**entry))
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


@router.get("/runs", response_model=list[RegistryRunResponse])
def list_runs(
    limit: int = Query(50, ge=1, le=500),
    step: str | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(DBRegistryRun)
    if step:
        query = query.filter(DBRegistryRun.step == step)
    # A run row is inserted when its step starts, so the id order is the start order.
    return query.order_by(DBRegistryRun.id.desc()).limit(limit).all()


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
