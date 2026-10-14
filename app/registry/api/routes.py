"""Registry endpoints, all system admin only: run stats can hold region names and proxy errors."""

from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.security import get_role_min_system_admin
from app.db.database import get_db
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider, DBRegistryRun
from app.registry.schemas import RegistryModelResponse, RegistryRunResponse

router = APIRouter(
    prefix="/registry", tags=["registry"], dependencies=[Depends(get_role_min_system_admin)]
)


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
    provider: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    if provider:
        query = query.filter(DBRegistryProvider.name == provider)
    if status:
        query = query.filter(DBRegistryModel.status == status)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(DBRegistryModel.model_id.ilike(f"%{escaped}%", escape="\\"))
    rows = (
        query.order_by(DBRegistryProvider.name, DBRegistryModel.model_id)
        .offset(offset)
        .limit(limit)
        .all()
    )

    prices = defaultdict(list)
    if rows:
        for price in (
            db.query(DBRegistryModelPrice)
            .filter(DBRegistryModelPrice.model_id.in_([model.id for model, _ in rows]))
            .order_by(DBRegistryModelPrice.scope_kind, DBRegistryModelPrice.scope)
        ):
            prices[price.model_id].append(price)

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
            source=model.source,
            first_seen=model.first_seen,
            last_seen=model.last_seen,
            price_scopes=prices[model.id],
        )
        for model, provider_name in rows
    ]
