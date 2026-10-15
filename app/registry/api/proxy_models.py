"""The endpoints for models on proxies, on the registry router.

Every write changes only the registry tables. Nothing calls a proxy.
"""

from collections import defaultdict
from datetime import date

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.routes import (
    _deployment_responses,
    _find_model,
    _find_proxy,
    _lock_proxies,
    _model_query,
    _save,
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
from app.registry.rules import (
    backend_problem,
    default_backend,
    enable_problems,
    removal_problem,
    support_rows,
)
from app.registry.schemas import (
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


@router.put("/proxies/{proxy}/models/{key:path}", response_model=list[RegistryModelDeploymentResponse])
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
            _alias_guard(db, region.id, [old_name], [deployments[0].id])
        enabled = changes.get("enabled")
        if enabled is False and not force and any(d.enabled for d in deployments):
            if problem := removal_problem(model, date.today()):
                raise HTTPException(status_code=409, detail=problem)
        # A new backend string on a deployment that is on after the PUT can be one the region cannot run.
        # A change of only groups, credential or model_name keeps the backend, so it skips rule 4.
        checked = [
            d
            for d in deployments
            if changes.get("enabled", d.enabled)
            and (not d.enabled or changes.get("litellm_model", d.litellm_model) != d.litellm_model)
        ]
        if checked:
            check_enable(Availability(db), [changes.get("litellm_model", d.litellm_model) for d in checked])

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
    _save(db, dry_run)
    return result


@router.delete("/proxies/{proxy}/models/{key:path}", status_code=204)
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
    _save(db, dry_run)
    return Response(status_code=204)


@router.post("/rollouts", response_model=list[RegistryRolloutEntry])
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
                    if not deployment.enabled and not force:
                        problems += enable_problems(
                            model, provider, support, availability, cloud_regions.get(provider),
                            deployment.litellm_model,
                        )
            else:
                action, model_name = "created", model.model_id
                backend = default_backend(provider, model.model_id, cloud_regions, availability)
                if (region_id, model_name) in aliases:
                    problems.append(_alias_name_problem(model_name))
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
    _save(db, dry_run)
    return entries
