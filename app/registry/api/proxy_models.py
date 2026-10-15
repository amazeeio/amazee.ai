"""The endpoints for models on proxies, on the registry router.

A write changes only the registry tables. Only a dry run reads the proxies that it touches.
"""

from collections import defaultdict
from datetime import date

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.dry_run import _save, deployment_plans
from app.registry.api.routes import (
    _deployment_responses,
    _find_model,
    _find_proxy,
    _lock_proxies,
    _model_query,
    _split_key,
    router,
)
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
from app.registry.planner import DEPLOYMENT_FIELDS
from app.registry.rules import (
    backend_problem,
    default_backend,
    enable_problems,
    geo_problem,
    removal_problem,
    support_rows,
)
from app.registry.schemas import (
    RegistryDryRun,
    RegistryModelDeploymentResponse,
    RegistryProxyModelUpdate,
    RegistryRollout,
    RegistryRolloutEntry,
)
from app.registry.support import Availability


def _require_imported(proxies, names: dict[int, str]) -> None:
    """Refuse a write on a proxy that the importer has not read.

    The import would add the live deployments next to the rows this API wrote, so one model would have two rows.
    """
    missing = sorted(names[proxy.region_id] for proxy in proxies if proxy.imported_at is None)
    if missing:
        raise HTTPException(status_code=409, detail=f"Import the proxy first: {', '.join(missing)}")


def _group_ids(db: Session, slugs: list[str]) -> dict[str, int]:
    """The ids of the groups, locked until the links are written.

    FOR KEY SHARE waits for a group PUT or DELETE that holds the group, so the link write cannot deadlock with
    it or fail on a deleted group. A group deleted in the meantime is then unknown. The sorted order keeps
    the lock order the same in every call.
    """
    found = dict(
        db.query(DBRegistryAccessGroup.slug, DBRegistryAccessGroup.id)
        .filter(DBRegistryAccessGroup.slug.in_(slugs))
        .order_by(DBRegistryAccessGroup.slug)
        .with_for_update(read=True, key_share=True)
    )
    unknown = sorted(set(slugs) - found.keys())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown access groups: {', '.join(unknown)}")
    return found


def _alias_names(db: Session, region_ids) -> set[tuple[int, str]]:
    return set(
        db.query(DBRegistryAlias.region_id, DBRegistryAlias.alias).filter(DBRegistryAlias.region_id.in_(region_ids))
    )


def _alias_name_problem(name: str) -> str:
    # LiteLLM would send the calls for the alias to this deployment, or the other way round.
    return f"the model name {name} is an alias on this proxy"


def _name_owners(db: Session, region_ids, names) -> dict[tuple[int, str], set[int]]:
    """The ids of the models that have a deployment with each of the names, per proxy."""
    owners = defaultdict(set)
    for region_id, name, model_id in db.query(
        DBRegistryModelRegion.region_id, DBRegistryModelRegion.model_name, DBRegistryModelRegion.model_id
    ).filter(DBRegistryModelRegion.region_id.in_(region_ids), DBRegistryModelRegion.model_name.in_(names)):
        owners[(region_id, name)].add(model_id)
    return owners


def _used_name_problem(owners, region_id: int, name: str, model_id: int) -> str | None:
    # LiteLLM would send the calls for the name to either model. Deployments of one model may share a name.
    if owners.get((region_id, name), set()) - {model_id}:
        return f"the model name {name} is used by another model on this proxy"
    return None


def _alias_guard(db: Session, region_id: int, names, changed_ids: list[int]) -> None:
    """Refuse a delete or a rename that leaves an alias on the proxy with no target. `force` never skips it."""
    names = set(names)
    kept = {
        name
        for (name,) in db.query(DBRegistryModelRegion.model_name).filter(
            DBRegistryModelRegion.region_id == region_id,
            DBRegistryModelRegion.model_name.in_(names),
            DBRegistryModelRegion.id.not_in(changed_ids),
        )
    }
    # A name that another deployment keeps still works as a target.
    left = names - kept
    if not left:
        return
    aliases = [
        alias
        for (alias,) in db.query(DBRegistryAlias.alias)
        .filter(DBRegistryAlias.region_id == region_id, DBRegistryAlias.target.in_(left))
        .order_by(DBRegistryAlias.alias)
    ]
    if aliases:
        raise HTTPException(
            status_code=409,
            detail=f"Aliases on this proxy point to this model name: {', '.join(aliases)}. Move or remove them first",
        )


def _deployments(db: Session, model: DBRegistryModel, region_id: int) -> list[DBRegistryModelRegion]:
    return (
        db.query(DBRegistryModelRegion)
        .filter_by(model_id=model.id, region_id=region_id)
        .order_by(DBRegistryModelRegion.model_name, DBRegistryModelRegion.id)
        .all()
    )


def _set_credential(deployment: DBRegistryModelRegion, name: str | None) -> None:
    # A new dict, because the JSON column does not see a change in place.
    params = {k: v for k, v in (deployment.litellm_params or {}).items() if k != "litellm_credential_name"}
    if name is not None:
        params["litellm_credential_name"] = name
    deployment.litellm_params = params


def _add_deployment(
    db: Session, model: DBRegistryModel, region_id: int, model_name: str, backend: str, credential: str | None,
    enabled: bool,
) -> DBRegistryModelRegion:
    # No LiteLLM deployment id and no price yet: the proxy has not seen this deployment.
    deployment = DBRegistryModelRegion(
        model_id=model.id, region_id=region_id, model_name=model_name, litellm_model=backend,
        prices={}, model_info={}, enabled=enabled,
    )
    _set_credential(deployment, credential)
    db.add(deployment)
    return deployment


@router.put(
    "/proxies/{proxy}/models/{key:path}",
    response_model=list[RegistryModelDeploymentResponse] | RegistryDryRun[list[RegistryModelDeploymentResponse]],
)
def set_proxy_model(
    proxy: str,
    key: str,
    response: Response,
    body: RegistryProxyModelUpdate | None = None,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    region = _find_proxy(db, proxy)
    model, provider = _find_model(db, key)
    proxy_row = _lock_proxies(db, [region.id])[region.id]
    _require_imported([proxy_row], {region.id: region.name})
    cloud_regions = proxy_row.cloud_regions or {}
    changes = (body or RegistryProxyModelUpdate()).model_dump(exclude_unset=True)
    group_ids = _group_ids(db, changes["groups"]) if "groups" in changes else None
    deployments = _deployments(db, model, region.id)

    def check_enable(availability: Availability, backends: list[str]) -> None:
        if force:
            return
        support = support_rows(db, [model.id], [region.id]).get((model.id, region.id))
        problems = [
            problem
            for backend in backends
            for problem in enable_problems(model, provider, support, availability, cloud_regions.get(provider), backend)
        ]
        if problems:
            raise HTTPException(status_code=422, detail=list(dict.fromkeys(problems)))

    # Every check runs before the first change, so a 4xx changes nothing.
    if not deployments:
        availability = Availability(db)
        name = changes.get("model_name", model.model_id)
        backend = changes.get("litellm_model") or default_backend(provider, model.model_id, cloud_regions, availability)
        if (region.id, name) in _alias_names(db, [region.id]):
            raise HTTPException(status_code=422, detail=_alias_name_problem(name))
        if problem := _used_name_problem(_name_owners(db, [region.id], [name]), region.id, name, model.id):
            raise HTTPException(status_code=422, detail=problem)
        if problem := backend_problem(backend, provider, model.model_id, cloud_regions):
            raise HTTPException(status_code=422, detail=problem)
        enabled = changes.get("enabled", True)
        if enabled:
            check_enable(availability, [backend])
        deployments = [_add_deployment(db, model, region.id, name, backend, changes.get("credential"), enabled)]
        db.flush()
        response.status_code = 201
    else:
        if len(deployments) > 1 and changes.keys() & {"model_name", "litellm_model"}:
            raise HTTPException(
                status_code=422,
                detail="The model has several deployments on this proxy, so the change cannot pick one model_name "
                "or litellm_model",
            )
        if "litellm_model" in changes and (
            problem := backend_problem(changes["litellm_model"], provider, model.model_id, cloud_regions)
        ):
            raise HTTPException(status_code=422, detail=problem)
        old_name = deployments[0].model_name
        if changes.get("model_name", old_name) != old_name:
            if (region.id, changes["model_name"]) in _alias_names(db, [region.id]):
                raise HTTPException(status_code=422, detail=_alias_name_problem(changes["model_name"]))
            owners = _name_owners(db, [region.id], [changes["model_name"]])
            if problem := _used_name_problem(owners, region.id, changes["model_name"], model.id):
                raise HTTPException(status_code=422, detail=problem)
            _alias_guard(db, region.id, [old_name], [deployments[0].id])
        enabled = changes.get("enabled")
        if enabled is False and not force and any(d.enabled for d in deployments):
            if problem := removal_problem(model, date.today()):
                raise HTTPException(status_code=409, detail=problem)
        # A new backend string on a deployment that is on after the PUT can be one the region cannot run.
        # A change of only groups, credential or model_name keeps the backend, so it skips rules 4 and 5.
        checked = [
            changes.get("litellm_model", d.litellm_model)
            for d in deployments
            if changes.get("enabled", d.enabled)
            and (not d.enabled or changes.get("litellm_model", d.litellm_model) != d.litellm_model)
        ]
        # A stored backend gets the region part of rule 5; a new one had the full check above. Force skips neither.
        if "litellm_model" not in changes:
            for backend in checked:
                if problem := geo_problem(backend, provider, cloud_regions):
                    raise HTTPException(status_code=422, detail=problem)
        if checked:
            check_enable(Availability(db), checked)

        for deployment in deployments:
            for field in ("model_name", "litellm_model", "enabled"):
                if field in changes:
                    setattr(deployment, field, changes[field])
            if "credential" in changes:
                _set_credential(deployment, changes["credential"])
        if group_ids is not None:
            db.query(DBRegistryModelRegionGroup).filter(
                DBRegistryModelRegionGroup.model_region_id.in_([d.id for d in deployments])
            ).delete(synchronize_session=False)

    if group_ids:
        db.add_all(
            DBRegistryModelRegionGroup(model_region_id=d.id, access_group_id=group_id)
            for d in deployments
            for group_id in group_ids.values()
        )
    db.flush()
    # The model's deployments on the proxy, in the shape of the model detail.
    prices = db.query(DBRegistryModelPrice).filter_by(model_id=model.id).all()
    result = _deployment_responses(
        db, prices, provider, [(d, region.name) for d in _deployments(db, model, region.id)]
    )
    return _save(
        db, dry_run, result, response=response, plan=lambda: deployment_plans(db, deployments, DEPLOYMENT_FIELDS)
    )


@router.delete(
    "/proxies/{proxy}/models/{key:path}",
    response_model=RegistryDryRun[None],
    responses={204: {"description": "The model's deployments on the proxy are deleted"}},
)
def remove_proxy_model(
    proxy: str,
    key: str,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    region = _find_proxy(db, proxy)
    model, _ = _find_model(db, key)
    _require_imported(_lock_proxies(db, [region.id]).values(), {region.id: region.name})
    deployments = _deployments(db, model, region.id)
    if not deployments:
        raise HTTPException(status_code=404, detail="The model has no deployment on this proxy")
    ids = [d.id for d in deployments]
    _alias_guard(db, region.id, [d.model_name for d in deployments], ids)
    if not force and (problem := removal_problem(model, date.today())):
        raise HTTPException(status_code=409, detail=problem)
    # The foreign key of the group links cascades, so this also removes them.
    db.query(DBRegistryModelRegion).filter(DBRegistryModelRegion.id.in_(ids)).delete(synchronize_session=False)
    # The rows were loaded before the delete, so the plan still reads their attributes.
    return _save(
        db, dry_run, Response(status_code=204),
        plan=lambda: deployment_plans(db, deployments, DEPLOYMENT_FIELDS, removed=True),
    )


@router.post(
    "/rollouts", response_model=list[RegistryRolloutEntry] | RegistryDryRun[list[RegistryRolloutEntry]]
)
def roll_out(
    body: RegistryRollout,
    dry_run: bool = Query(False),
    force: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Put each model on each proxy, all or nothing. The query count does not grow with the pairs."""
    regions = {
        region.name: region
        for region in db.query(DBRegion)
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .filter(DBRegion.name.in_(body.proxies))
    }
    if unknown := [name for name in body.proxies if name not in regions]:
        raise HTTPException(status_code=422, detail=f"Unknown proxies: {', '.join(unknown)}")
    region_ids = [region.id for region in regions.values()]
    proxies = _lock_proxies(db, region_ids)
    # One proxy that is not imported refuses the whole call, before the model check: the admin imports it,
    # then runs the rollout again.
    _require_imported(proxies.values(), {region.id: region.name for region in regions.values()})
    keys = list(dict.fromkeys(body.models))
    idents = {_split_key(key) for key in keys}
    models = {
        f"{provider}/{model.model_id}": (model, provider)
        for model, provider in _model_query(db).filter(
            tuple_(DBRegistryProvider.name, DBRegistryModel.model_id).in_(idents)
        )
    }
    if unknown := [key for key in keys if key not in models]:
        raise HTTPException(status_code=422, detail=f"Unknown models: {', '.join(unknown)}")
    group_ids = _group_ids(db, body.groups)

    model_ids = [model.id for model, _ in models.values()]
    deployments: dict[tuple[int, int], list[DBRegistryModelRegion]] = defaultdict(list)
    for row in (
        db.query(DBRegistryModelRegion)
        .filter(DBRegistryModelRegion.model_id.in_(model_ids), DBRegistryModelRegion.region_id.in_(region_ids))
        .order_by(DBRegistryModelRegion.model_name, DBRegistryModelRegion.id)
    ):
        deployments[(row.model_id, row.region_id)].append(row)
    links = set(
        db.query(DBRegistryModelRegionGroup.model_region_id, DBRegistryModelRegionGroup.access_group_id).filter(
            DBRegistryModelRegionGroup.model_region_id.in_([d.id for rows in deployments.values() for d in rows])
        )
    )
    supports = support_rows(db, model_ids, region_ids)
    availability = Availability(db)
    aliases = _alias_names(db, region_ids)
    owners = _name_owners(db, region_ids, [model.model_id for model, _ in models.values()])

    # Every check runs before the first write, so a 422 changes nothing.
    entries, pairs = [], []
    for key in keys:
        model, provider = models[key]
        for name in body.proxies:
            region_id = regions[name].id
            cloud_regions = proxies[region_id].cloud_regions or {}
            support = supports.get((model.id, region_id))
            existing = deployments.get((model.id, region_id))
            problems = []
            if existing:
                action, model_name, backend = "updated", existing[0].model_name, existing[0].litellm_model
                for deployment in existing:
                    if deployment.enabled:
                        continue
                    # The region part of rule 5 covers a stored backend too, and force never skips it.
                    if problem := geo_problem(deployment.litellm_model, provider, cloud_regions):
                        problems.append(problem)
                    if not force:
                        problems += enable_problems(
                            model, provider, support, availability, cloud_regions.get(provider),
                            deployment.litellm_model,
                        )
            else:
                action, model_name = "created", model.model_id
                backend = default_backend(provider, model.model_id, cloud_regions, availability)
                if (region_id, model_name) in aliases:
                    problems.append(_alias_name_problem(model_name))
                if problem := _used_name_problem(owners, region_id, model_name, model.id):
                    problems.append(problem)
                # Two models of this rollout with the same model id must not share the name either.
                owners[(region_id, model_name)].add(model.id)
                if problem := backend_problem(backend, provider, model.model_id, cloud_regions):
                    problems.append(problem)
                if not force:
                    problems += enable_problems(
                        model, provider, support, availability, cloud_regions.get(provider), backend
                    )
            entries.append(RegistryRolloutEntry(
                proxy=name, model=key, action=action, model_name=model_name, litellm_model=backend,
                problems=list(dict.fromkeys(problems)),
            ))
            pairs.append((model, region_id, existing))
    if any(entry.problems for entry in entries):
        raise HTTPException(status_code=422, detail=[entry.model_dump() for entry in entries])

    targets = []
    for entry, (model, region_id, existing) in zip(entries, pairs, strict=True):
        if existing:
            # The credential of a deployment that exists stays as it is.
            for deployment in existing:
                deployment.enabled = True
        else:
            existing = [_add_deployment(
                db, model, region_id, entry.model_name, entry.litellm_model, body.proxies[entry.proxy], True,
            )]
        targets += existing
    db.flush()
    # Add the groups and keep the links the deployments already have.
    db.add_all(
        DBRegistryModelRegionGroup(model_region_id=deployment.id, access_group_id=group_id)
        for deployment in targets
        for group_id in group_ids.values()
        if (deployment.id, group_id) not in links
    )
    return _save(db, dry_run, entries, plan=lambda: deployment_plans(db, targets, DEPLOYMENT_FIELDS))
