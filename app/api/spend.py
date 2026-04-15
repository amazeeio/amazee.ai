import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.limit_service import DEFAULT_MAX_SPEND, LimitService
from app.core.roles import UserRole
from app.core.litellm_user_sync import _team_role_for_litellm
from app.core.security import (
    get_current_user_from_auth,
    get_private_ai_access,
    get_role_min_team_admin,
)
from app.db.database import get_db
from app.db.models import DBPrivateAIKey, DBRegion, DBTeam, DBTeamRegion, DBUser
from app.schemas.limits import ResourceType
from app.schemas.models import (
    PrivateAIKeySpend,
    SpendBudgetUpdateRequest,
    SpendBudgetUpdateResponse,
    SpendKeyItem,
    TeamSpendResponse,
    UserSpendResponse,
)
from app.services.litellm import LiteLLMService

router = APIRouter(tags=["spend"])
logger = logging.getLogger(__name__)


def _to_int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_token_usage(data: dict) -> tuple[int | None, int | None, int | None]:
    return (
        _to_int_or_none(data.get("prompt_tokens")),
        _to_int_or_none(data.get("completion_tokens")),
        _to_int_or_none(data.get("total_tokens")),
    )


def _sum_optional_token_values(
    items: list[SpendKeyItem],
) -> tuple[int | None, int | None, int | None]:
    prompt_sum = sum(i.prompt_tokens for i in items if i.prompt_tokens is not None)
    completion_sum = sum(
        i.completion_tokens for i in items if i.completion_tokens is not None
    )
    total_sum = sum(i.total_tokens for i in items if i.total_tokens is not None)
    has_prompt = any(i.prompt_tokens is not None for i in items)
    has_completion = any(i.completion_tokens is not None for i in items)
    has_total = any(i.total_tokens is not None for i in items)
    return (
        prompt_sum if has_prompt else None,
        completion_sum if has_completion else None,
        total_sum if has_total else None,
    )


def _get_region_or_404(db: Session, region_id: int) -> DBRegion:
    region = (
        db.query(DBRegion)
        .filter(DBRegion.id == region_id, DBRegion.is_active.is_(True))
        .first()
    )
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    return region


def _assert_team_access(current_user: DBUser, role: str, team_id: int) -> None:
    if current_user.is_admin:
        return
    if role not in [UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.READ_ONLY]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )
    if current_user.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )


def _assert_user_access(current_user: DBUser, role: str, target_user: DBUser) -> None:
    if current_user.is_admin:
        return
    if target_user.id == current_user.id:
        return
    if role in [UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.READ_ONLY]:
        if (
            current_user.team_id is not None
            and current_user.team_id == target_user.team_id
        ):
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to perform this action",
    )


def _assert_team_budget_write_access(
    current_user: DBUser, role: str, team_id: int
) -> None:
    if current_user.is_admin:
        return
    if role != UserRole.TEAM_ADMIN or current_user.team_id != team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )


def _assert_user_budget_write_access(
    current_user: DBUser, role: str, target_user: DBUser
) -> None:
    if current_user.is_admin:
        return
    if (
        role != UserRole.TEAM_ADMIN
        or current_user.team_id is None
        or current_user.team_id != target_user.team_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )


def _assert_team_region_association(
    db: Session, region: DBRegion, team_id: int
) -> None:
    if not region.is_dedicated:
        return
    association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.region_id == region.id, DBTeamRegion.team_id == team_id)
        .first()
    )
    if not association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is not associated with this dedicated region",
        )


async def _get_key_spend_items(
    keys: list[DBPrivateAIKey], region: DBRegion
) -> tuple[list[SpendKeyItem], float, float]:
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    items: list[SpendKeyItem] = []
    total_spend = 0.0
    total_budget = 0.0

    for key in keys:
        spend = float(key.cached_spend or 0.0)
        max_budget = None
        prompt_tokens = None
        completion_tokens = None
        total_tokens = None
        if key.litellm_token:
            try:
                key_data = await service.get_key_info(key.litellm_token)
                info = key_data.get("info", {})
                spend = float(info.get("spend", 0.0) or 0.0)
                max_budget = info.get("max_budget")
                prompt_tokens, completion_tokens, total_tokens = _extract_token_usage(
                    info
                )
            except Exception as exc:
                # Keep fallback spend from cached_spend.
                logger.warning(
                    "Falling back to cached spend for key_id=%s region_id=%s due to LiteLLM error: %s",
                    key.id,
                    region.id,
                    str(exc),
                )

        total_spend += spend
        if max_budget is not None:
            total_budget += float(max_budget or 0.0)
        items.append(
            SpendKeyItem(
                key_id=key.id,
                key_name=key.name,
                owner_id=key.owner_id,
                team_id=key.team_id,
                spend=round(spend, 4),
                max_budget=float(max_budget) if max_budget is not None else None,
                cached_spend=key.cached_spend,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
        )

    return items, round(total_spend, 4), round(total_budget, 4)


def _find_db_key_id_for_litellm_key(
    db: Session, region_id: int, litellm_key: dict, fallback_team_id: int | None = None
) -> int | None:
    metadata = litellm_key.get("metadata") or {}
    key_name = metadata.get("amazeeai_private_ai_key_name")
    owner_raw = litellm_key.get("user_id")
    owner_id = int(owner_raw) if owner_raw and str(owner_raw).isdigit() else None
    query = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.region_id == region_id)
    if key_name:
        query = query.filter(DBPrivateAIKey.name == key_name)
    if owner_id is not None:
        query = query.filter(DBPrivateAIKey.owner_id == owner_id)
    elif fallback_team_id is not None:
        query = query.filter(DBPrivateAIKey.team_id == fallback_team_id)
    db_key = query.order_by(DBPrivateAIKey.id.desc()).first()
    if not db_key:
        logger.warning(
            "Unable to map LiteLLM key to DB key: region_id=%s key_name=%s owner_id=%s fallback_team_id=%s",
            region_id,
            key_name,
            owner_id,
            fallback_team_id,
        )
    return db_key.id if db_key else None


@router.get("/{region_id}/team/{team_id}", response_model=TeamSpendResponse)
async def get_team_spend(
    region_id: int,
    team_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_private_ai_access),
    db: Session = Depends(get_db),
):
    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _assert_team_access(current_user, user_role, team_id)
    region = _get_region_or_404(db, region_id)

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    items: list[SpendKeyItem] = []
    total_budget = 0.0
    total_prompt_tokens = None
    total_completion_tokens = None
    total_tokens = None

    try:
        team_data = await service.get_team_info(lite_team_id)
        team_info = team_data.get("team_info", team_data)
        total_spend = round(float(team_info.get("spend", 0.0) or 0.0), 4)
        (
            total_prompt_tokens,
            total_completion_tokens,
            total_tokens,
        ) = _extract_token_usage(team_info)
        max_budget = team_info.get("max_budget")
        if max_budget is not None:
            total_budget = round(float(max_budget or 0.0), 4)
        for litellm_key in team_data.get("keys", []):
            db_key_id = _find_db_key_id_for_litellm_key(
                db=db,
                region_id=region_id,
                litellm_key=litellm_key,
                fallback_team_id=team_id,
            )
            items.append(
                SpendKeyItem(
                    key_id=db_key_id,
                    key_name=(litellm_key.get("metadata") or {}).get(
                        "amazeeai_private_ai_key_name"
                    ),
                    owner_id=(
                        int(litellm_key.get("user_id"))
                        if str(litellm_key.get("user_id", "")).isdigit()
                        else None
                    ),
                    team_id=team_id,
                    spend=round(float(litellm_key.get("spend", 0.0) or 0.0), 4),
                    max_budget=(
                        float(litellm_key.get("max_budget"))
                        if litellm_key.get("max_budget") is not None
                        else None
                    ),
                    cached_spend=None,
                    prompt_tokens=_to_int_or_none(litellm_key.get("prompt_tokens")),
                    completion_tokens=_to_int_or_none(
                        litellm_key.get("completion_tokens")
                    ),
                    total_tokens=_to_int_or_none(litellm_key.get("total_tokens")),
                )
            )
    except Exception as exc:
        logger.warning(
            "Falling back to DB-derived team spend for team_id=%s region_id=%s due to LiteLLM error: %s",
            team_id,
            region_id,
            str(exc),
        )
        team_users = db.query(DBUser).filter(DBUser.team_id == team_id).all()
        team_user_ids = [u.id for u in team_users]
        keys = (
            db.query(DBPrivateAIKey)
            .filter(
                DBPrivateAIKey.region_id == region_id,
                (DBPrivateAIKey.team_id == team_id)
                | (DBPrivateAIKey.owner_id.in_(team_user_ids)),
            )
            .all()
        )
        items, total_spend, total_budget = await _get_key_spend_items(keys, region)
        (
            total_prompt_tokens,
            total_completion_tokens,
            total_tokens,
        ) = _sum_optional_token_values(items)
        if total_budget == 0.0 and len(keys) > 0:
            limit_service = LimitService(db)
            try:
                default_budget = limit_service.get_default_team_limit_for_resource(
                    ResourceType.BUDGET
                )
            except Exception as exc:
                default_budget = DEFAULT_MAX_SPEND
                logger.warning(
                    "Using default budget fallback for team_id=%s due to limit service error: %s",
                    team_id,
                    str(exc),
                )
            total_budget = round(float(default_budget or 0.0) * len(keys), 4)

    return TeamSpendResponse(
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        team_name=team.name,
        total_spend=total_spend,
        total_budget=total_budget,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        key_count=len(items),
        keys=items,
    )


@router.get("/{region_id}/user/{user_id}", response_model=UserSpendResponse)
async def get_user_spend(
    region_id: int,
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_private_ai_access),
    db: Session = Depends(get_db),
):
    target_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_user_access(current_user, user_role, target_user)
    region = _get_region_or_404(db, region_id)

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    items: list[SpendKeyItem] = []
    total_spend = None
    total_prompt_tokens = None
    total_completion_tokens = None
    total_tokens = None
    try:
        user_data = await service.get_user_info(str(user_id))
        user_info = user_data.get("user_info", {})
        total_spend = round(float(user_info.get("spend", 0.0) or 0.0), 4)
        (
            total_prompt_tokens,
            total_completion_tokens,
            total_tokens,
        ) = _extract_token_usage(user_info)
        for litellm_key in user_data.get("keys", []):
            db_key_id = _find_db_key_id_for_litellm_key(
                db=db,
                region_id=region_id,
                litellm_key=litellm_key,
                fallback_team_id=target_user.team_id,
            )
            items.append(
                SpendKeyItem(
                    key_id=db_key_id,
                    key_name=(litellm_key.get("metadata") or {}).get(
                        "amazeeai_private_ai_key_name"
                    ),
                    owner_id=user_id,
                    team_id=target_user.team_id,
                    spend=round(float(litellm_key.get("spend", 0.0) or 0.0), 4),
                    max_budget=(
                        float(litellm_key.get("max_budget"))
                        if litellm_key.get("max_budget") is not None
                        else None
                    ),
                    cached_spend=None,
                    prompt_tokens=_to_int_or_none(litellm_key.get("prompt_tokens")),
                    completion_tokens=_to_int_or_none(
                        litellm_key.get("completion_tokens")
                    ),
                    total_tokens=_to_int_or_none(litellm_key.get("total_tokens")),
                )
            )
    except Exception as exc:
        logger.warning(
            "Falling back to DB-derived user spend for user_id=%s region_id=%s due to LiteLLM error: %s",
            user_id,
            region_id,
            str(exc),
        )
        keys = (
            db.query(DBPrivateAIKey)
            .filter(
                DBPrivateAIKey.region_id == region_id,
                DBPrivateAIKey.owner_id == user_id,
            )
            .all()
        )
        items, total_spend, _ = await _get_key_spend_items(keys, region)
        (
            total_prompt_tokens,
            total_completion_tokens,
            total_tokens,
        ) = _sum_optional_token_values(items)

    return UserSpendResponse(
        region_id=region_id,
        region_name=region.name,
        user_id=user_id,
        team_id=target_user.team_id,
        team_name=target_user.team.name if target_user.team else None,
        total_spend=total_spend,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        total_tokens=total_tokens,
        key_count=len(items),
        keys=items,
    )


@router.get("/{region_id}/key/{key_id}", response_model=PrivateAIKeySpend)
async def get_key_spend_alias(
    region_id: int,
    key_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_private_ai_access),
    db: Session = Depends(get_db),
):
    key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == key_id).first()
    if not key:
        raise HTTPException(status_code=404, detail="Private AI Key not found")
    if key.region_id != region_id:
        raise HTTPException(
            status_code=404, detail="Private AI Key not found in region"
        )

    # Reuse authorization semantics from private-ai-keys endpoints.
    if current_user.is_admin:
        pass
    elif user_role in [UserRole.TEAM_ADMIN, UserRole.KEY_CREATOR, UserRole.READ_ONLY]:
        if key.team_id is not None:
            if key.team_id != current_user.team_id:
                raise HTTPException(status_code=404, detail="Private AI Key not found")
        else:
            owner = db.query(DBUser).filter(DBUser.id == key.owner_id).first()
            if not owner or owner.team_id != current_user.team_id:
                raise HTTPException(status_code=404, detail="Private AI Key not found")
    else:
        if key.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Private AI Key not found")

    region = _get_region_or_404(db, region_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    try:
        data = await service.get_key_info(key.litellm_token)
        info = data.get("info", {})
        return PrivateAIKeySpend.model_validate(
            {"spend": info.get("spend", 0.0), **info}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Private AI Key spend: {str(exc)}",
        )


@router.put(
    "/{region_id}/team/{team_id}/budget", response_model=SpendBudgetUpdateResponse
)
async def update_team_budget(
    region_id: int,
    team_id: int,
    body: SpendBudgetUpdateRequest,
    current_user: DBUser = Depends(get_current_user_from_auth),
    role: str = Depends(get_role_min_team_admin),
    db: Session = Depends(get_db),
):
    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _assert_team_budget_write_access(current_user, role, team_id)
    region = _get_region_or_404(db, region_id)
    _assert_team_region_association(db, region, team_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)

    await service.update_team_budget(
        team_id=lite_team_id,
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
    )
    info = await service.get_team_info(lite_team_id)
    team_info = info.get("team_info", info)
    return SpendBudgetUpdateResponse(
        scope="team",
        source_endpoint="/team/update",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        max_budget=team_info.get("max_budget"),
        budget_duration=team_info.get("budget_duration"),
        note="For team keys, team budget governs spend enforcement.",
    )


@router.put(
    "/{region_id}/team/{team_id}/member/{user_id}/budget",
    response_model=SpendBudgetUpdateResponse,
)
async def update_team_member_budget(
    region_id: int,
    team_id: int,
    user_id: int,
    body: SpendBudgetUpdateRequest,
    current_user: DBUser = Depends(get_current_user_from_auth),
    role: str = Depends(get_role_min_team_admin),
    db: Session = Depends(get_db),
):
    team = (
        db.query(DBTeam)
        .filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None))
        .first()
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    user = (
        db.query(DBUser)
        .filter(DBUser.id == user_id, DBUser.is_active.is_(True))
        .first()
    )
    if not user or user.team_id != team_id:
        raise HTTPException(status_code=404, detail="User not found in team")
    _assert_team_budget_write_access(current_user, role, team_id)
    region = _get_region_or_404(db, region_id)
    _assert_team_region_association(db, region, team_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)

    if body.max_budget is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="max_budget is required for team-member budget updates",
        )

    await service.update_team_member(
        team_id=lite_team_id,
        user_id=str(user_id),
        role=_team_role_for_litellm(user),
        max_budget_in_team=body.max_budget,
    )
    return SpendBudgetUpdateResponse(
        scope="team_member",
        source_endpoint="/team/member_update",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        user_id=user_id,
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
        note="This budget is scoped to the user within the specified team.",
    )


@router.put(
    "/{region_id}/key/{key_id}/budget", response_model=SpendBudgetUpdateResponse
)
async def update_key_budget(
    region_id: int,
    key_id: int,
    body: SpendBudgetUpdateRequest,
    current_user: DBUser = Depends(get_current_user_from_auth),
    role: str = Depends(get_role_min_team_admin),
    db: Session = Depends(get_db),
):
    key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == key_id).first()
    if not key or key.region_id != region_id:
        raise HTTPException(
            status_code=404, detail="Private AI Key not found in region"
        )
    if key.team_id is not None:
        _assert_team_budget_write_access(current_user, role, key.team_id)
    else:
        owner = db.query(DBUser).filter(DBUser.id == key.owner_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Key owner not found")
        _assert_user_budget_write_access(current_user, role, owner)

    region = _get_region_or_404(db, region_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    await service.update_key_budget(
        litellm_token=key.litellm_token,
        budget_duration=body.budget_duration,
        max_budget=body.max_budget,
        clear_max_budget=body.max_budget is None,
    )
    key_info = await service.get_key_info(key.litellm_token)
    info = key_info.get("info", {})
    return SpendBudgetUpdateResponse(
        scope="key",
        source_endpoint="/key/update",
        region_id=region_id,
        region_name=region.name,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
        max_budget=info.get("max_budget"),
        budget_duration=info.get("budget_duration"),
        note="If key has team_id, team/team-member budgets may take precedence during enforcement.",
    )
