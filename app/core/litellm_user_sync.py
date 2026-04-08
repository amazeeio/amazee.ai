import logging
from typing import Iterable, List

from app.db.models import DBRegion, DBTeamRegion, DBUser
from app.services.litellm import LiteLLMService
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _dedupe_regions(regions: Iterable[DBRegion]) -> List[DBRegion]:
    seen: set[int] = set()
    deduped: List[DBRegion] = []
    for region in regions:
        if region.id in seen:
            continue
        seen.add(region.id)
        deduped.append(region)
    return deduped


def _team_role_for_litellm(db_user: DBUser) -> str:
    # LiteLLM OSS rejects team admin assignment via API (enterprise-only feature).
    # Keep API writes stable by mapping all team roles to LiteLLM "user".
    return "user"


def get_target_regions_for_user(db: Session, team_id: int | None) -> List[DBRegion]:
    shared_regions = (
        db.query(DBRegion)
        .filter(DBRegion.is_active.is_(True), DBRegion.is_dedicated.is_(False))
        .all()
    )
    if team_id is None:
        return shared_regions

    dedicated_regions = (
        db.query(DBRegion)
        .join(DBTeamRegion, DBTeamRegion.region_id == DBRegion.id)
        .filter(
            DBRegion.is_active.is_(True),
            DBRegion.is_dedicated.is_(True),
            DBTeamRegion.team_id == team_id,
        )
        .all()
    )
    return _dedupe_regions([*shared_regions, *dedicated_regions])


async def sync_create_user_across_regions(
    db: Session,
    db_user: DBUser,
    team_id: int | None = None,
    force_regions: List[DBRegion] | None = None,
) -> None:
    regions = force_regions or get_target_regions_for_user(db, team_id)
    for region in regions:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        await service.create_user(
            user_id=str(db_user.id),
            user_email=db_user.email,
            auto_create_key=False,
        )
        if team_id is not None:
            lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
            await service.add_team_member(
                team_id=lite_team_id,
                user_id=str(db_user.id),
                role=_team_role_for_litellm(db_user),
            )


async def sync_add_user_to_team(
    db: Session,
    db_user: DBUser,
    team_id: int,
    force_regions: List[DBRegion] | None = None,
) -> None:
    regions = force_regions or get_target_regions_for_user(db, team_id)
    for region in regions:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        await service.create_user(
            user_id=str(db_user.id),
            user_email=db_user.email,
            auto_create_key=False,
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
        await service.add_team_member(
            team_id=lite_team_id,
            user_id=str(db_user.id),
            role=_team_role_for_litellm(db_user),
        )


async def sync_remove_user_from_team(
    db: Session, db_user: DBUser, team_id: int, force_regions: List[DBRegion] | None = None
) -> None:
    regions = force_regions or get_target_regions_for_user(db, team_id)
    for region in regions:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
        await service.remove_team_member(team_id=lite_team_id, user_id=str(db_user.id))


async def sync_update_user_team_role(
    db: Session, db_user: DBUser, team_id: int, force_regions: List[DBRegion] | None = None
) -> None:
    regions = force_regions or get_target_regions_for_user(db, team_id)
    role = _team_role_for_litellm(db_user)
    for region in regions:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
        await service.update_team_member(
            team_id=lite_team_id, user_id=str(db_user.id), role=role
        )


async def sync_delete_user_across_regions(
    db: Session, db_user: DBUser, team_id: int | None = None
) -> None:
    regions = get_target_regions_for_user(db, team_id)
    for region in regions:
        service = LiteLLMService(
            api_url=region.litellm_api_url, api_key=region.litellm_api_key
        )
        if team_id is not None:
            lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
            await service.remove_team_member(team_id=lite_team_id, user_id=str(db_user.id))
        await service.delete_user(user_id=str(db_user.id))
