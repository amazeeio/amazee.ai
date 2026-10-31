"""The access group and alias endpoints, on the registry router."""

from collections import defaultdict

from fastapi import Depends, HTTPException, Path, Query, Response
from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
# The registry's only read of an old catalog table, for the group slugs. It goes when the old catalog goes.
from app.db.models import DBModelAccessGroup, DBRegion
from app.registry.api.dry_run import alias_plans, deployment_plans
from app.registry.api.sync import _save
from app.registry.api.routes import Refused, _find_proxy, _lock_proxies, _split_key, refuse, router
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryAlias,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryProvider,
    DBRegistryProxy,
)
from app.registry.schemas import (
    RegistryAccessGroupCreate,
    RegistryAccessGroupModelResponse,
    RegistryAccessGroupModels,
    RegistryAccessGroupResponse,
    RegistryAccessGroupUpdate,
    RegistryAliasResponse,
    RegistryAliasUpdate,
    RegistryDryRun,
    RegistryProblem,
)


# No `/`, so an alias is one segment of its URL.
ALIAS_PATTERN = r"^[A-Za-z0-9._:-]{1,128}$"


def _group_responses(db: Session, group_ids: list[int] | None = None) -> list[RegistryAccessGroupResponse]:
    """The groups by slug, with the models that carry each, in two queries."""
    groups = db.query(DBRegistryAccessGroup).order_by(DBRegistryAccessGroup.slug)
    links = (
        db.query(
            DBRegistryModelRegionGroup.access_group_id,
            DBRegion.name,
            DBRegistryProvider.name,
            DBRegistryModel.model_id,
            DBRegistryModelRegion.model_name,
        )
        .join(DBRegistryModelRegion, DBRegistryModelRegion.id == DBRegistryModelRegionGroup.model_region_id)
        .join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
        .join(DBRegistryModel, DBRegistryModel.id == DBRegistryModelRegion.model_id)
        .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
        .order_by(DBRegion.name, DBRegistryProvider.name, DBRegistryModel.model_id, DBRegistryModelRegion.model_name)
        # Load-balanced deployments share one model_name.
        .distinct()
    )
    if group_ids is not None:
        groups = groups.filter(DBRegistryAccessGroup.id.in_(group_ids))
        links = links.filter(DBRegistryModelRegionGroup.access_group_id.in_(group_ids))

    # The query order is already sorted, and dicts keep it.
    models: dict[int, dict[tuple[str, str], list[str]]] = defaultdict(dict)
    for group_id, proxy, provider, model_id, model_name in links:
        models[group_id].setdefault((proxy, f"{provider}/{model_id}"), []).append(model_name)

    return [
        RegistryAccessGroupResponse(
            slug=group.slug,
            label=group.label,
            description=group.description,
            models=[
                RegistryAccessGroupModelResponse(proxy=proxy, model=key, model_names=names)
                for (proxy, key), names in models[group.id].items()
            ],
        )
        for group in groups
    ]


def _find_group(db: Session, slug: str, lock: bool = False) -> DBRegistryAccessGroup:
    query = db.query(DBRegistryAccessGroup).filter(DBRegistryAccessGroup.slug == slug)
    group = (query.with_for_update() if lock else query).one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Access group not found")
    return group


@router.get("/access-groups", response_model=list[RegistryAccessGroupResponse])
def list_access_groups(response: Response, db: Session = Depends(get_db)):
    groups = _group_responses(db)
    response.headers["X-Total-Count"] = str(len(groups))
    return groups


@router.post(
    "/access-groups",
    status_code=201,
    response_model=RegistryAccessGroupResponse | RegistryDryRun[RegistryAccessGroupResponse],
    responses={200: {"model": RegistryDryRun[RegistryAccessGroupResponse], "description": "The dry run"}},
)
def create_access_group(
    body: RegistryAccessGroupCreate,
    response: Response,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    group = DBRegistryAccessGroup(**body.model_dump())
    db.add(group)
    # The unique slug, not a lookup first, so two requests at the same time cannot both create it.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Refused([RegistryProblem(message="Access group already exists", status=409)]) from None
    # A new group is a label only: the sync sends no group on its own, so there is no plan.
    return _save(db, dry_run, _group_responses(db, [group.id])[0], response=response)


@router.patch(
    "/access-groups/{slug}",
    response_model=RegistryAccessGroupResponse | RegistryDryRun[RegistryAccessGroupResponse],
)
def update_access_group(
    slug: str,
    body: RegistryAccessGroupUpdate,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    group = _find_group(db, slug)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    db.flush()
    # The label and the description are not synced, so there is no plan.
    return _save(db, dry_run, _group_responses(db, [group.id])[0])


def _deployment_ids(db: Session, refs: list[tuple[str, str]]) -> set[int]:
    """Every deployment of each (proxy, model key), in one query; a 422 names every bad entry."""
    idents = {(proxy, *_split_key(key)) for proxy, key in refs}
    deployments: dict[tuple[str, str], set[int]] = defaultdict(set)
    for proxy, provider, model_id, row_id in (
        db.query(DBRegion.name, DBRegistryProvider.name, DBRegistryModel.model_id, DBRegistryModelRegion.id)
        .join(DBRegion, DBRegion.id == DBRegistryModelRegion.region_id)
        # A region with no registry_proxies row is not a proxy of the registry.
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .join(DBRegistryModel, DBRegistryModel.id == DBRegistryModelRegion.model_id)
        .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
        .filter(tuple_(DBRegion.name, DBRegistryProvider.name, DBRegistryModel.model_id).in_(idents))
        # Lock each deployment before its links, the same order as a delete of the deployment, so the two
        # cannot deadlock. A deployment deleted in the meantime is then not found and gets the 422 below.
        .order_by(DBRegistryModelRegion.id)
        .with_for_update(read=True, key_share=True, of=DBRegistryModelRegion)
    ):
        deployments[(proxy, f"{provider}/{model_id}")].add(row_id)

    refuse([
        RegistryProblem(message=f"{key} has no deployment on {proxy}", proxy=proxy, model=key)
        for proxy, key in refs
        if (proxy, key) not in deployments
    ])
    return set().union(*deployments.values())


@router.put(
    "/access-groups/{slug}/models",
    response_model=RegistryAccessGroupResponse | RegistryDryRun[RegistryAccessGroupResponse],
)
def set_access_group_models(
    slug: str,
    body: RegistryAccessGroupModels,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    # The route deletes and inserts every link, so two PUTs on one group at the same time
    # would insert the same rows twice. The row lock makes them run one after the other.
    group = _find_group(db, slug, lock=True)
    refs = [(ref.proxy, ref.model) for ref in body.models]
    # Every check runs before the first write, so a 422 changes nothing.
    wanted = _deployment_ids(db, refs) if refs else set()
    current = {
        row_id
        for (row_id,) in db.query(DBRegistryModelRegionGroup.model_region_id).filter_by(access_group_id=group.id)
    }
    db.query(DBRegistryModelRegionGroup).filter_by(access_group_id=group.id).delete()
    db.add_all(DBRegistryModelRegionGroup(model_region_id=row_id, access_group_id=group.id) for row_id in wanted)
    db.flush()

    def plan():
        # Only the deployments that gain or lose the group change on a proxy.
        rows = (
            db.query(DBRegistryModelRegion)
            .filter(DBRegistryModelRegion.id.in_(current ^ wanted))
            .order_by(DBRegistryModelRegion.id)
            .all()
        )
        return deployment_plans(db, rows, frozenset({"access_groups"}), live_only=True)

    return _save(db, dry_run, _group_responses(db, [group.id])[0], plan=plan)


@router.delete(
    "/access-groups/{slug}",
    response_model=RegistryDryRun[None],
    responses={204: {"description": "The access group is deleted"}},
)
def delete_access_group(slug: str, dry_run: bool = Query(False), db: Session = Depends(get_db)):
    # The lock stops a PUT from linking the group between the check below and the delete.
    group = _find_group(db, slug, lock=True)
    problems = []
    # Teams still point at the old catalog's group until the registry pushes groups itself.
    if db.query(DBModelAccessGroup.id).filter(DBModelAccessGroup.slug == slug).first():
        problems.append(RegistryProblem(message="The old catalog still has this access group", status=409))
    if db.query(DBRegistryModelRegionGroup).filter(DBRegistryModelRegionGroup.access_group_id == group.id).first():
        problems.append(RegistryProblem(message="Deployments still carry this access group", status=409))
    refuse(problems)
    db.delete(group)
    # No plan: the 409 above means that no deployment carries the group, so no deployment changes.
    return _save(db, dry_run, Response(status_code=204))


@router.get("/aliases", response_model=list[RegistryAliasResponse])
def list_aliases(response: Response, proxy: str | None = Query(None), db: Session = Depends(get_db)):
    query = db.query(DBRegistryAlias, DBRegion.name).join(DBRegion, DBRegion.id == DBRegistryAlias.region_id)
    if proxy:
        query = query.filter(DBRegion.name == proxy)
    aliases = [
        RegistryAliasResponse(proxy=name, alias=row.alias, target=row.target, hidden=row.hidden)
        for row, name in query.order_by(DBRegion.name, DBRegistryAlias.alias)
    ]
    response.headers["X-Total-Count"] = str(len(aliases))
    return aliases


# `{alias:path}`: a name with a `/` then reaches the pattern and gets a 422, not a 404.
@router.put(
    "/aliases/{proxy}/{alias:path}",
    response_model=RegistryAliasResponse | RegistryDryRun[RegistryAliasResponse],
    responses={201: {"model": RegistryAliasResponse, "description": "The alias is created"}},
)
def set_alias(
    proxy: str,
    body: RegistryAliasUpdate,
    response: Response,
    alias: str = Path(pattern=ALIAS_PATTERN),
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    region = _find_proxy(db, proxy)
    # The alias guard of a rename or a delete reads the aliases under the same lock. The lock also makes
    # alias creates on one proxy run one after the other, so two creates of one alias cannot both insert.
    _lock_proxies(db, [region.id])
    names = {
        name
        for (name,) in db.query(DBRegistryModelRegion.model_name).filter(DBRegistryModelRegion.region_id == region.id)
    }
    problems = []
    # LiteLLM would send the calls for a real model name to another model.
    if alias in names:
        problems.append(RegistryProblem(message="The alias is the name of a deployment on this proxy", proxy=proxy))
    if body.target not in names:
        problems.append(RegistryProblem(message="The target is not a deployment on this proxy", proxy=proxy))
    refuse(problems)

    row = db.query(DBRegistryAlias).filter_by(region_id=region.id, alias=alias).one_or_none()
    if row is None:
        db.add(DBRegistryAlias(region_id=region.id, alias=alias, target=body.target, hidden=body.hidden))
        response.status_code = 201
    else:
        row.target = body.target
        row.hidden = body.hidden
    result = RegistryAliasResponse(proxy=region.name, alias=alias, target=body.target, hidden=body.hidden)
    return _save(db, dry_run, result, response=response, plan=lambda: alias_plans(db, region))


@router.delete(
    "/aliases/{proxy}/{alias:path}",
    response_model=RegistryDryRun[None],
    responses={204: {"description": "The alias is deleted"}},
)
def delete_alias(proxy: str, alias: str, dry_run: bool = Query(False), db: Session = Depends(get_db)):
    region = _find_proxy(db, proxy)
    row = db.query(DBRegistryAlias).filter_by(region_id=region.id, alias=alias).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Alias not found")
    db.delete(row)
    return _save(db, dry_run, Response(status_code=204), plan=lambda: alias_plans(db, region, alias))
