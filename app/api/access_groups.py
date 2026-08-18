from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Iterable, List, Optional
import logging

from app.db.database import get_db
from app.core.security import get_role_min_system_admin
from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBModelRegion,
    DBRegion,
    DBTeam,
    DBTeamGroupSyncRun,
    DBTeamModelAccessGroup,
    DBUser,
)
from app.schemas.models import (
    AccessGroupCreate,
    AccessGroupResponse,
    AccessGroupUpdate,
    RegionDefaultAccessGroupRequest,
    TeamAccessGroupsResponse,
    TeamAccessGroupsUpdateRequest,
    TeamGroupSyncRunResponse,
)
from app.services.access_groups import sync_region_teams_task, sync_team_groups_task
from app.services.model_sync import sync_model_to_region_task

logger = logging.getLogger(__name__)

router = APIRouter(tags=["access_groups"])


def _group_response(db: Session, group: DBModelAccessGroup) -> AccessGroupResponse:
    default_in = (
        db.query(DBRegion.id).filter(DBRegion.default_access_group_id == group.id).all()
    )
    return AccessGroupResponse(
        id=group.id,
        slug=group.slug,
        label=group.label,
        description=group.description,
        model_ids=sorted(a.model_id for a in group.model_associations),
        region_ids=sorted(a.region_id for a in group.region_associations),
        default_in_region_ids=sorted(r[0] for r in default_in),
        team_count=len(group.team_associations),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _resync_models(
    db: Session, background_tasks: BackgroundTasks, model_pks: Iterable[int]
) -> None:
    """Mark every active region deployment of the given models pending and
    queue a model sync — re-pushing model_info.access_groups is part of the
    normal model sync, so tag changes ride the existing machinery."""
    model_pks = set(model_pks)
    if not model_pks:
        return
    assocs = (
        db.query(DBModelRegion)
        .join(DBModel, DBModel.id == DBModelRegion.model_id)
        .filter(
            DBModelRegion.model_id.in_(model_pks),
            DBModelRegion.is_active.is_(True),
            DBModel.is_active_globally.is_(True),
            DBModel.deleted_at.is_(None),
        )
        .all()
    )
    for assoc in assocs:
        assoc.sync_status = "pending"
        assoc.sync_error = None
        background_tasks.add_task(sync_model_to_region_task, assoc.model_id, assoc.region_id)
    db.commit()


def _team_regions(db: Session, team: DBTeam) -> List[DBRegion]:
    """All active regions a team belongs to (teams.region_id + team_regions)."""
    regions = {r.id: r for r in team.allowed_regions if r and r.is_active}
    if team.region_id:
        region = db.query(DBRegion).filter_by(id=team.region_id).first()
        if region and region.is_active:
            regions[region.id] = region
    return list(regions.values())


def _resync_teams(
    db: Session, background_tasks: BackgroundTasks, team_ids: Iterable[int]
) -> None:
    """Queue a group re-sync for each team in every enforced region it belongs to."""
    for team_id in set(team_ids):
        team = db.query(DBTeam).filter_by(id=team_id).first()
        if not team:
            continue
        for region in _team_regions(db, team):
            if region.default_access_group_id is not None:
                background_tasks.add_task(sync_team_groups_task, team_id, region.id)


def _validate_model_ids(db: Session, model_ids: List[int]) -> None:
    if not model_ids:
        return
    found = {
        row[0]
        for row in db.query(DBModel.id)
        .filter(DBModel.id.in_(model_ids), DBModel.deleted_at.is_(None))
        .all()
    }
    missing = sorted(set(model_ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or deleted model ids: {missing}",
        )


def _validate_region_ids(db: Session, region_ids: List[int]) -> None:
    if not region_ids:
        return
    found = {row[0] for row in db.query(DBRegion.id).filter(DBRegion.id.in_(region_ids)).all()}
    missing = sorted(set(region_ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region ids: {missing}",
        )


def _get_group(db: Session, group_id: int) -> DBModelAccessGroup:
    group = db.query(DBModelAccessGroup).filter_by(id=group_id).first()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Access group with ID {group_id} not found.",
        )
    return group


@router.get("/admin/access-groups", response_model=List[AccessGroupResponse])
async def list_access_groups(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    groups = db.query(DBModelAccessGroup).order_by(DBModelAccessGroup.slug).all()
    return [_group_response(db, g) for g in groups]


@router.post(
    "/admin/access-groups",
    response_model=AccessGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_access_group(
    group_in: AccessGroupCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    if db.query(DBModelAccessGroup).filter_by(slug=group_in.slug).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Access group with slug '{group_in.slug}' already exists.",
        )
    _validate_model_ids(db, group_in.model_ids)
    _validate_region_ids(db, group_in.region_ids)

    group = DBModelAccessGroup(
        slug=group_in.slug, label=group_in.label, description=group_in.description
    )
    db.add(group)
    db.flush()
    for model_id in set(group_in.model_ids):
        db.add(DBModelAccessGroupModel(group_id=group.id, model_id=model_id))
    for region_id in set(group_in.region_ids):
        db.add(DBModelAccessGroupRegion(group_id=group.id, region_id=region_id))
    db.commit()
    db.refresh(group)

    _resync_models(db, background_tasks, group_in.model_ids)
    return _group_response(db, group)


@router.get("/admin/access-groups/{group_id}", response_model=AccessGroupResponse)
async def get_access_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    return _group_response(db, _get_group(db, group_id))


@router.put("/admin/access-groups/{group_id}", response_model=AccessGroupResponse)
async def update_access_group(
    group_id: int,
    group_in: AccessGroupUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    group = _get_group(db, group_id)
    affected_model_pks: set[int] = set()
    affected_team_ids: set[int] = set()

    if group_in.label is not None:
        group.label = group_in.label
    if group_in.description is not None:
        group.description = group_in.description

    if group_in.model_ids is not None:
        _validate_model_ids(db, group_in.model_ids)
        old_ids = {a.model_id for a in group.model_associations}
        new_ids = set(group_in.model_ids)
        if old_ids != new_ids:
            affected_model_pks |= old_ids | new_ids
            db.query(DBModelAccessGroupModel).filter_by(group_id=group.id).delete()
            for model_id in new_ids:
                db.add(DBModelAccessGroupModel(group_id=group.id, model_id=model_id))

    if group_in.region_ids is not None:
        _validate_region_ids(db, group_in.region_ids)
        old_regions = {a.region_id for a in group.region_associations}
        new_regions = set(group_in.region_ids)
        if old_regions != new_regions:
            # A region whose default points at this group must stay deployed —
            # undeploying it would strip every team there of its default set.
            default_regions = {
                row[0]
                for row in db.query(DBRegion.id)
                .filter(DBRegion.default_access_group_id == group.id)
                .all()
            }
            blocked = sorted(default_regions - new_regions)
            if blocked:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot undeploy group '{group.slug}' from region ids {blocked}: "
                        "it is the default access group there. Change the region default first."
                    ),
                )
            # Tags change in added/removed regions for all members (old or new)
            affected_model_pks |= {a.model_id for a in group.model_associations}
            if group_in.model_ids is not None:
                affected_model_pks |= set(group_in.model_ids)
            # Opt-in visibility of this group changes in added/removed regions
            affected_team_ids |= {a.team_id for a in group.team_associations}
            db.query(DBModelAccessGroupRegion).filter_by(group_id=group.id).delete()
            for region_id in new_regions:
                db.add(DBModelAccessGroupRegion(group_id=group.id, region_id=region_id))

    db.commit()
    db.refresh(group)

    _resync_models(db, background_tasks, affected_model_pks)
    _resync_teams(db, background_tasks, affected_team_ids)
    return _group_response(db, group)


@router.delete("/admin/access-groups/{group_id}", status_code=status.HTTP_200_OK)
async def delete_access_group(
    group_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    group = _get_group(db, group_id)

    default_regions = (
        db.query(DBRegion.name).filter(DBRegion.default_access_group_id == group.id).all()
    )
    if default_regions:
        names = sorted(r[0] for r in default_regions)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete group '{group.slug}': it is the default access group for "
                f"region(s) {names}. Point those regions at another default first."
            ),
        )

    member_model_pks = {a.model_id for a in group.model_associations}
    attached_team_ids = {a.team_id for a in group.team_associations}
    slug = group.slug

    db.delete(group)
    db.commit()

    # Member models drop the tag; previously attached teams drop the slug.
    _resync_models(db, background_tasks, member_model_pks)
    _resync_teams(db, background_tasks, attached_team_ids)
    logger.info(
        f"Deleted access group '{slug}' ({len(member_model_pks)} models untagged, "
        f"{len(attached_team_ids)} teams detached)"
    )
    return {
        "status": "success",
        "models_untagged": len(member_model_pks),
        "teams_detached": len(attached_team_ids),
    }


@router.put("/admin/regions/{region_id}/default-access-group")
async def set_region_default_access_group(
    region_id: int,
    request_in: RegionDefaultAccessGroupRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Set (or clear) a region's default access group — the enforcement switch.

    Setting it starts a fan-out that writes group-based `models` lists to every
    team in the region. Clearing it (group_id=null) fans out an empty list,
    rolling teams back to legacy all-models behavior.
    """
    region = db.query(DBRegion).filter(DBRegion.id == region_id, DBRegion.is_active.is_(True)).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {region_id} not found.",
        )

    if request_in.group_id == region.default_access_group_id:
        return {"status": "unchanged", "run_id": None}

    if request_in.group_id is not None:
        group = _get_group(db, request_in.group_id)
        deployed = (
            db.query(DBModelAccessGroupRegion)
            .filter_by(group_id=group.id, region_id=region.id)
            .first()
        )
        if not deployed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group '{group.slug}' is not deployed to region '{region.name}'.",
            )
        # Gate: every member model active in this region must be sync-green,
        # otherwise teams get restricted to tags that haven't landed yet.
        member_assocs = (
            db.query(DBModel.model_id, DBModelRegion.sync_status)
            .join(DBModelRegion, DBModelRegion.model_id == DBModel.id)
            .join(DBModelAccessGroupModel, DBModelAccessGroupModel.model_id == DBModel.id)
            .filter(
                DBModelAccessGroupModel.group_id == group.id,
                DBModelRegion.region_id == region.id,
                DBModelRegion.is_active.is_(True),
                DBModel.deleted_at.is_(None),
            )
            .all()
        )
        if not member_assocs:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Group '{group.slug}' has no active models in region '{region.name}'; "
                    "making it the default would leave every team without models."
                ),
            )
        unsynced = sorted(m for m, s in member_assocs if s != "synced")
        if unsynced:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Group '{group.slug}' has models not yet synced to region "
                    f"'{region.name}': {unsynced}. Wait for or retry those syncs first."
                ),
            )

    region.default_access_group_id = request_in.group_id
    run = DBTeamGroupSyncRun(region_id=region.id)
    db.add(run)
    db.commit()
    db.refresh(run)

    background_tasks.add_task(sync_region_teams_task, run.id)
    return {"status": "success", "run_id": run.id}


@router.get(
    "/admin/regions/{region_id}/team-group-sync-run",
    response_model=Optional[TeamGroupSyncRunResponse],
)
async def get_latest_team_group_sync_run(
    region_id: int,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Latest team fan-out run for a region (None if never run)."""
    return (
        db.query(DBTeamGroupSyncRun)
        .filter_by(region_id=region_id)
        .order_by(DBTeamGroupSyncRun.id.desc())
        .first()
    )


@router.post(
    "/admin/regions/{region_id}/team-group-sync-run",
    response_model=TeamGroupSyncRunResponse,
)
async def retry_team_group_sync_run(
    region_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Start a fresh fan-out for the region (retry). Safe to run anytime —
    each team's list is recomputed from the DB."""
    region = db.query(DBRegion).filter(DBRegion.id == region_id, DBRegion.is_active.is_(True)).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {region_id} not found.",
        )
    run = DBTeamGroupSyncRun(region_id=region.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(sync_region_teams_task, run.id)
    return run


def _get_team(db: Session, team_id: int) -> DBTeam:
    team = db.query(DBTeam).filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None)).first()
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team with ID {team_id} not found.",
        )
    return team


def _team_access_groups_response(db: Session, team: DBTeam) -> TeamAccessGroupsResponse:
    opt_ins = (
        db.query(DBModelAccessGroup.slug)
        .join(DBTeamModelAccessGroup, DBTeamModelAccessGroup.group_id == DBModelAccessGroup.id)
        .filter(DBTeamModelAccessGroup.team_id == team.id)
        .all()
    )
    defaults = {}
    for region in _team_regions(db, team):
        if region.default_access_group is not None:
            defaults[region.name] = region.default_access_group.slug
    return TeamAccessGroupsResponse(
        team_id=team.id,
        access_groups=sorted(row[0] for row in opt_ins),
        defaults=defaults,
    )


@router.get("/admin/teams/{team_id}/access-groups", response_model=TeamAccessGroupsResponse)
async def get_team_access_groups(
    team_id: int,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    return _team_access_groups_response(db, _get_team(db, team_id))


@router.put("/admin/teams/{team_id}/access-groups", response_model=TeamAccessGroupsResponse)
async def set_team_access_groups(
    team_id: int,
    request_in: TeamAccessGroupsUpdateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Replace a team's opt-in access groups (declarative set-list; the region
    default is implicit and must not be included). This is the MOAD target."""
    team = _get_team(db, team_id)

    enforced_regions = [
        r for r in _team_regions(db, team) if r.default_access_group_id is not None
    ]
    if not enforced_regions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Team belongs to no region with access-group enforcement enabled "
                "(no default access group set); opt-ins would have no effect."
            ),
        )

    slugs = set(request_in.access_groups)
    groups = (
        db.query(DBModelAccessGroup).filter(DBModelAccessGroup.slug.in_(slugs)).all()
        if slugs
        else []
    )
    missing = sorted(slugs - {g.slug for g in groups})
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown access group slugs: {missing}",
        )
    enforced_region_ids = {r.id for r in enforced_regions}
    for group in groups:
        deployed_region_ids = {a.region_id for a in group.region_associations}
        if not (deployed_region_ids & enforced_region_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Group '{group.slug}' is not deployed to any of the team's "
                    "enforced regions."
                ),
            )

    db.query(DBTeamModelAccessGroup).filter_by(team_id=team.id).delete()
    for group in groups:
        db.add(DBTeamModelAccessGroup(team_id=team.id, group_id=group.id))
    db.commit()

    for region in enforced_regions:
        background_tasks.add_task(sync_team_groups_task, team.id, region.id)

    return _team_access_groups_response(db, team)
