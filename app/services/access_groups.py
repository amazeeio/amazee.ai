"""Model access-group helpers and team-side sync.

A group's slug is the only thing that exists on LiteLLM: it is written into
each member model's model_info.access_groups (by the model sync) and into each
team's `models` list (here). Everything below is a pure function of DB state,
so every sync is idempotent and safe to re-run.
"""

import logging
import traceback
from datetime import datetime, UTC

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import (
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBRegion,
    DBTeam,
    DBTeamGroupSyncRun,
    DBTeamModelAccessGroup,
    DBTeamRegion,
)
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)


def model_access_group_slugs(db: Session, model_pk: int, region_id: int) -> list[str]:
    """Slugs for a model deployment in a region: groups that contain the model
    AND are deployed to that region."""
    rows = (
        db.query(DBModelAccessGroup.slug)
        .join(DBModelAccessGroupModel, DBModelAccessGroupModel.group_id == DBModelAccessGroup.id)
        .join(DBModelAccessGroupRegion, DBModelAccessGroupRegion.group_id == DBModelAccessGroup.id)
        .filter(
            DBModelAccessGroupModel.model_id == model_pk,
            DBModelAccessGroupRegion.region_id == region_id,
        )
        .all()
    )
    return sorted(row[0] for row in rows)


def effective_team_group_slugs(db: Session, team_id: int, region: DBRegion) -> list[str] | None:
    """The team's LiteLLM `models` list for a region: region default group +
    the team's opt-in groups that are deployed to that region.

    Returns None when the region has no default group (enforcement off) —
    callers must then leave the team's `models` untouched (or clear it).
    """
    if region.default_access_group_id is None:
        return None

    default_slug = (
        db.query(DBModelAccessGroup.slug)
        .filter(DBModelAccessGroup.id == region.default_access_group_id)
        .scalar()
    )
    opt_in_slugs = (
        db.query(DBModelAccessGroup.slug)
        .join(DBTeamModelAccessGroup, DBTeamModelAccessGroup.group_id == DBModelAccessGroup.id)
        .join(DBModelAccessGroupRegion, DBModelAccessGroupRegion.group_id == DBModelAccessGroup.id)
        .filter(
            DBTeamModelAccessGroup.team_id == team_id,
            DBModelAccessGroupRegion.region_id == region.id,
        )
        .all()
    )
    slugs = {default_slug} | {row[0] for row in opt_in_slugs}
    return sorted(slugs)


def region_team_ids(db: Session, region_id: int) -> list[int]:
    """Teams belonging to a region: via teams.region_id or team_regions."""
    rows = (
        db.query(DBTeam.id)
        .outerjoin(DBTeamRegion, DBTeamRegion.team_id == DBTeam.id)
        .filter(
            DBTeam.deleted_at.is_(None),
            or_(DBTeam.region_id == region_id, DBTeamRegion.region_id == region_id),
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


async def sync_team_groups(db: Session, team_id: int, region: DBRegion) -> None:
    """Push one team's effective group list to one region's LiteLLM proxy.

    When enforcement is off (no default group) the restriction is cleared
    ([] = all-proxy-models on LiteLLM), so turning enforcement off rolls
    teams back to legacy behavior.
    """
    slugs = effective_team_group_slugs(db, team_id, region)
    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    await service.update_team_models(lite_team_id, slugs if slugs is not None else [])


async def sync_team_groups_task(team_id: int, region_id: int) -> None:
    """Background task: sync a single team's groups to a region."""
    db = None
    try:
        db = next(get_db())
        region = db.query(DBRegion).filter_by(id=region_id).first()
        if not region or not region.is_active:
            logger.error(f"Team group sync skipped: region {region_id} missing or inactive")
            return
        await sync_team_groups(db, team_id, region)
        logger.info(f"Synced access groups for team {team_id} to region {region.name}")
    except Exception as e:
        logger.error(f"Team group sync failed for team {team_id}, region {region_id}: {e}\n{traceback.format_exc()}")
    finally:
        if db:
            db.close()


async def sync_region_teams_task(run_id: int) -> None:
    """Background fan-out: re-write the `models` list of every team in the
    run's region. Idempotent — each team's list is recomputed from the DB, so
    a crashed or partially-failed run is fixed by simply starting a new one.
    """
    db = None
    try:
        db = next(get_db())
        run = db.query(DBTeamGroupSyncRun).filter_by(id=run_id).first()
        if not run:
            logger.error(f"Team group sync run {run_id} not found")
            return
        region = db.query(DBRegion).filter_by(id=run.region_id).first()
        if not region or not region.is_active:
            run.status = "failed"
            run.error_sample = f"Region {run.region_id} missing or inactive"
            run.finished_at = datetime.now(UTC)
            db.commit()
            return

        team_ids = region_team_ids(db, region.id)
        run.total = len(team_ids)
        db.commit()

        failed: list[int] = []
        error_sample = None
        for team_id in team_ids:
            try:
                await sync_team_groups(db, team_id, region)
            except Exception as e:
                # A team that was never provisioned on this proxy fails here;
                # that's expected for stale DB rows — record and continue.
                failed.append(team_id)
                error_sample = error_sample or str(e)
                logger.warning(f"Team group sync run {run_id}: team {team_id} failed: {e}")
            run.done += 1
            if run.done % 25 == 0:
                run.failed_team_ids = list(failed)
                db.commit()

        run.failed_team_ids = list(failed)
        run.error_sample = error_sample
        run.status = "failed" if failed else "done"
        run.finished_at = datetime.now(UTC)
        db.commit()
        logger.info(
            f"Team group sync run {run_id} for region {region.name} finished: "
            f"{run.done}/{run.total} done, {len(failed)} failed"
        )
    except Exception as e:
        logger.error(f"Team group sync run {run_id} crashed: {e}\n{traceback.format_exc()}")
        if db:
            try:
                db.rollback()
                run = db.query(DBTeamGroupSyncRun).filter_by(id=run_id).first()
                if run:
                    run.status = "failed"
                    run.error_sample = str(e)
                    run.finished_at = datetime.now(UTC)
                    db.commit()
            except Exception as inner_e:
                logger.error(f"Failed to record sync run failure: {inner_e}")
    finally:
        if db:
            db.close()
