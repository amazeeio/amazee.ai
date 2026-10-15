"""The access group and alias endpoints, on the registry router."""

from collections import defaultdict

from fastapi import Depends, HTTPException, Query, Response
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
)
from app.registry.schemas import (
    RegistryAccessGroupCreate,
    RegistryAccessGroupModelResponse,
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
