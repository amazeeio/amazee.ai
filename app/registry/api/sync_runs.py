"""The sync-run endpoints, the proxy repair and the retry, on the registry router."""

from collections import defaultdict

from fastapi import BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.dry_run import plan_steps
from app.registry.api.proxy_models import _import_problems
from app.registry.api.routes import _find_proxy, _paginate, refuse, router
from app.registry.api.sync import create_run, execute_run, full_scope, retry_scope, scope_plans
from app.registry.config import registry_manages
from app.registry.models import DBRegistryProxy, DBRegistrySyncRun, DBRegistrySyncStep
from app.registry.schemas import (
    RegistryDryRun,
    RegistryProblem,
    RegistrySyncRunDetailResponse,
    RegistrySyncRunResponse,
    RegistrySyncStarted,
    RegistrySyncStepResponse,
)

_STARTED = {202: {"model": RegistrySyncStarted[None], "description": "A sync run started"}}


def _names(db: Session, region_ids) -> dict[int, str]:
    return dict(db.query(DBRegion.id, DBRegion.name).filter(DBRegion.id.in_(set(region_ids))))


def _run_response(run: DBRegistrySyncRun, names: dict[int, str]) -> dict:
    return {
        "id": run.id, "trigger": run.trigger, "status": run.status,
        "proxies": sorted(names[rid] for rid in run.region_ids or [] if rid in names),
        "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
        "error": run.error, "stats": run.stats,
    }


def _find_run(db: Session, run_id: int) -> DBRegistrySyncRun:
    run = db.get(DBRegistrySyncRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return run


def _steps(db: Session, run_id: int) -> list[DBRegistrySyncStep]:
    return (
        db.query(DBRegistrySyncStep)
        .filter_by(run_id=run_id)
        .order_by(DBRegistrySyncStep.region_id, DBRegistrySyncStep.position)
        .all()
    )


def _start(
    db: Session, trigger: str, scope: dict, dry_run: bool, response: Response, background: BackgroundTasks
):
    """Start a run of the scope, or in a dry run, plan it and save nothing."""
    if dry_run:
        plans = scope_plans(db, scope)
        db.rollback()
        return RegistryDryRun(result=None, steps=plan_steps(plans))
    run = create_run(db, trigger, scope)
    db.commit()
    background.add_task(execute_run, run.id)
    response.status_code = 202
    return RegistrySyncStarted(sync_run_id=run.id, result=None)


@router.get("/sync-runs", response_model=list[RegistrySyncRunResponse])
def list_sync_runs(
    response: Response,
    proxy: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(DBRegistrySyncRun)
    if proxy:
        region_id = db.query(DBRegion.id).filter(DBRegion.name == proxy).scalar()
        query = query.filter(DBRegistrySyncRun.region_ids.contains([region_id]))
    if status:
        query = query.filter(DBRegistrySyncRun.status == status)
    runs = _paginate(query.order_by(DBRegistrySyncRun.id.desc()), response, offset, limit)
    names = _names(db, [rid for run in runs for rid in run.region_ids or []])
    return [_run_response(run, names) for run in runs]


@router.get("/sync-runs/{run_id}", response_model=RegistrySyncRunDetailResponse)
def get_sync_run(run_id: int, db: Session = Depends(get_db)):
    run = _find_run(db, run_id)
    steps = _steps(db, run_id)
    names = _names(db, [*(run.region_ids or []), *(step.region_id for step in steps)])
    statuses = defaultdict(set)
    for step in steps:
        statuses[step.region_id].add(step.status)
    finished = run.status in ("ok", "failed")
    results = {
        names.get(region_id, str(region_id)): (
            "failed" if "failed" in statuses[region_id]
            else "pending" if "pending" in statuses[region_id] or not finished
            else "ok"
        )
        for region_id in run.region_ids or []
    }
    return RegistrySyncRunDetailResponse(
        **_run_response(run, names),
        steps=[
            RegistrySyncStepResponse(
                proxy=names.get(step.region_id, str(step.region_id)), position=step.position, action=step.action,
                model=step.model, model_name=step.model_name, deployment_id=step.deployment_id,
                credential=step.credential, payload=step.payload, changes=step.changes, status=step.status,
                message=step.message, finished_at=step.finished_at,
            )
            for step in steps
        ],
        results=results,
    )


@router.post(
    "/sync-runs/{run_id}/retry",
    response_model=RegistrySyncStarted[None] | RegistryDryRun[None],
    responses=_STARTED,
)
def retry_sync_run(
    run_id: int,
    response: Response,
    background: BackgroundTasks,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    run = _find_run(db, run_id)
    steps = _steps(db, run_id)
    if run.status not in ("ok", "failed"):
        refuse([RegistryProblem(message="The sync run has not finished", status=409)])
    if not any(step.status == "failed" for step in steps):
        refuse([RegistryProblem(message="The sync run has no failed step", status=409)])
    return _start(db, "retry", retry_scope(run, steps), dry_run, response, background)


@router.post(
    "/proxies/{proxy}/sync",
    response_model=RegistrySyncStarted[None] | RegistryDryRun[None],
    responses=_STARTED,
)
def repair_proxy(
    proxy: str,
    response: Response,
    background: BackgroundTasks,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Repair every deployment, the alias map and the credentials of the proxy now."""
    region = _find_proxy(db, proxy)
    problems = []
    if not registry_manages(region.name):
        problems.append(RegistryProblem(message="The registry does not manage the proxy", proxy=proxy, status=409))
    problems += _import_problems([db.get(DBRegistryProxy, region.id)], {region.id: region.name})
    refuse(problems)
    return _start(db, "repair", full_scope([region.id]), dry_run, response, background)
