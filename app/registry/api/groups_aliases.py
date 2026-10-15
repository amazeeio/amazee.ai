"""The access group and alias endpoints, on the registry router."""

from collections import defaultdict

from fastapi import Depends, HTTPException, Query, Response
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import DBRegion
from app.registry.api.routes import _save, router
from app.registry.models import (
    DBRegistryAccessGroup,
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
)


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
    )
    if group_ids is not None:
        groups = groups.filter(DBRegistryAccessGroup.id.in_(group_ids))
        links = links.filter(DBRegistryModelRegionGroup.access_group_id.in_(group_ids))

    # The query order is already sorted, and dicts keep it.
    models: dict[int, dict[tuple[str, str], list[str]]] = defaultdict(dict)
    for group_id, proxy, provider, model_id, model_name in links:
        names = models[group_id].setdefault((proxy, f"{provider}/{model_id}"), [])
        # Load-balanced deployments share one model_name.
        if model_name not in names:
            names.append(model_name)

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


def _find_group(db: Session, slug: str) -> DBRegistryAccessGroup:
    group = db.query(DBRegistryAccessGroup).filter(DBRegistryAccessGroup.slug == slug).one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Access group not found")
    return group


@router.get("/access-groups", response_model=list[RegistryAccessGroupResponse])
def list_access_groups(response: Response, db: Session = Depends(get_db)):
    groups = _group_responses(db)
    response.headers["X-Total-Count"] = str(len(groups))
    return groups


@router.post("/access-groups", status_code=201, response_model=RegistryAccessGroupResponse)
def create_access_group(
    body: RegistryAccessGroupCreate,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    if db.query(DBRegistryAccessGroup).filter(DBRegistryAccessGroup.slug == body.slug).first():
        raise HTTPException(status_code=409, detail="Access group already exists")
    group = DBRegistryAccessGroup(**body.model_dump())
    db.add(group)
    db.flush()
    result = _group_responses(db, [group.id])[0]
    _save(db, dry_run)
    return result


@router.patch("/access-groups/{slug}", response_model=RegistryAccessGroupResponse)
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
    result = _group_responses(db, [group.id])[0]
    _save(db, dry_run)
    return result


def _deployment_ids(db: Session, refs: list[tuple[str, str]]) -> set[int]:
    """Every deployment of each (proxy, model key), in three queries; a 422 names the first bad entry."""
    regions = dict(
        db.query(DBRegion.name, DBRegion.id)
        .join(DBRegistryProxy, DBRegistryProxy.region_id == DBRegion.id)
        .filter(DBRegion.name.in_({proxy for proxy, _ in refs}))
    )
    for proxy, _ in refs:
        if proxy not in regions:
            raise HTTPException(status_code=422, detail=f"Unknown proxy: {proxy}")

    # Only the first slash splits: model ids can hold slashes (deepinfra/BAAI/bge-m3).
    idents = {key: key.partition("/")[::2] for _, key in refs}
    models = {
        (provider, model_id): row_id
        for row_id, provider, model_id in db.query(
            DBRegistryModel.id, DBRegistryProvider.name, DBRegistryModel.model_id
        )
        .join(DBRegistryProvider)
        .filter(tuple_(DBRegistryProvider.name, DBRegistryModel.model_id).in_(set(idents.values())))
    }
    for _, key in refs:
        if idents[key] not in models:
            raise HTTPException(status_code=422, detail=f"Unknown model: {key}")

    pairs = {(proxy, key): (regions[proxy], models[idents[key]]) for proxy, key in refs}
    deployments: dict[tuple[int, int], set[int]] = defaultdict(set)
    for row_id, region_id, model_id in db.query(
        DBRegistryModelRegion.id, DBRegistryModelRegion.region_id, DBRegistryModelRegion.model_id
    ).filter(tuple_(DBRegistryModelRegion.region_id, DBRegistryModelRegion.model_id).in_(set(pairs.values()))):
        deployments[(region_id, model_id)].add(row_id)
    wanted = set()
    for (proxy, key), pair in pairs.items():
        if pair not in deployments:
            raise HTTPException(status_code=422, detail=f"{key} has no deployment on {proxy}")
        wanted |= deployments[pair]
    return wanted


@router.put("/access-groups/{slug}/models", response_model=RegistryAccessGroupResponse)
def set_access_group_models(
    slug: str,
    body: RegistryAccessGroupModels,
    dry_run: bool = Query(False),
    db: Session = Depends(get_db),
):
    group = _find_group(db, slug)
    refs = list(dict.fromkeys((ref.proxy, ref.model) for ref in body.models))
    # Every check runs before the first write, so a 422 changes nothing.
    wanted = _deployment_ids(db, refs) if refs else set()
    current = {
        row_id
        for (row_id,) in db.query(DBRegistryModelRegionGroup.model_region_id).filter(
            DBRegistryModelRegionGroup.access_group_id == group.id
        )
    }
    if current - wanted:
        db.query(DBRegistryModelRegionGroup).filter(
            DBRegistryModelRegionGroup.access_group_id == group.id,
            DBRegistryModelRegionGroup.model_region_id.in_(current - wanted),
        ).delete(synchronize_session=False)
    db.add_all(
        DBRegistryModelRegionGroup(model_region_id=row_id, access_group_id=group.id) for row_id in wanted - current
    )
    db.flush()
    result = _group_responses(db, [group.id])[0]
    _save(db, dry_run)
    return result
