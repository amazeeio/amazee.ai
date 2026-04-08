from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.limit_service import DEFAULT_MAX_SPEND, LimitService
from app.core.roles import UserRole
from app.core.security import get_current_user_from_auth, get_private_ai_access
from app.db.database import get_db
from app.db.models import DBPrivateAIKey, DBRegion, DBTeam, DBUser
from app.schemas.limits import ResourceType
from app.schemas.models import PrivateAIKeySpend, SpendKeyItem, TeamSpendResponse, UserSpendResponse
from app.services.litellm import LiteLLMService

router = APIRouter(tags=["spend"])


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
        if current_user.team_id is not None and current_user.team_id == target_user.team_id:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to perform this action",
    )


async def _get_key_spend_items(
    keys: list[DBPrivateAIKey], region: DBRegion
) -> tuple[list[SpendKeyItem], float, float]:
    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    items: list[SpendKeyItem] = []
    total_spend = 0.0
    total_budget = 0.0

    for key in keys:
        spend = float(key.cached_spend or 0.0)
        max_budget = None
        if key.litellm_token:
            try:
                key_data = await service.get_key_info(key.litellm_token)
                info = key_data.get("info", {})
                spend = float(info.get("spend", 0.0) or 0.0)
                max_budget = info.get("max_budget")
            except Exception:
                # Keep fallback spend from cached_spend.
                pass

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
    return db_key.id if db_key else None


@router.get("/{region_id}/team/{team_id}", response_model=TeamSpendResponse)
async def get_team_spend(
    region_id: int,
    team_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    user_role: str = Depends(get_private_ai_access),
    db: Session = Depends(get_db),
):
    team = db.query(DBTeam).filter(DBTeam.id == team_id, DBTeam.deleted_at.is_(None)).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    _assert_team_access(current_user, user_role, team_id)
    region = _get_region_or_404(db, region_id)

    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    items: list[SpendKeyItem] = []
    total_spend = 0.0
    total_budget = 0.0

    try:
        team_data = await service.get_team_info(lite_team_id)
        team_info = team_data.get("team_info", team_data)
        total_spend = round(float(team_info.get("spend", 0.0) or 0.0), 4)
        max_budget = team_info.get("max_budget")
        if max_budget is not None:
            total_budget = round(float(max_budget or 0.0), 4)
        for litellm_key in team_data.get("keys", []):
            db_key_id = _find_db_key_id_for_litellm_key(
                db=db, region_id=region_id, litellm_key=litellm_key, fallback_team_id=team_id
            )
            items.append(
                SpendKeyItem(
                    key_id=db_key_id or -1,
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
                )
            )
    except Exception:
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
        if total_budget == 0.0 and len(keys) > 0:
            limit_service = LimitService(db)
            try:
                default_budget = limit_service.get_default_team_limit_for_resource(
                    ResourceType.BUDGET
                )
            except Exception:
                default_budget = DEFAULT_MAX_SPEND
            total_budget = round(float(default_budget or 0.0) * len(keys), 4)

    return TeamSpendResponse(
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        team_name=team.name,
        total_spend=total_spend,
        total_budget=total_budget,
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

    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    items: list[SpendKeyItem] = []
    total_spend = 0.0
    try:
        user_data = await service.get_user_info(str(user_id))
        user_info = user_data.get("user_info", {})
        total_spend = round(float(user_info.get("spend", 0.0) or 0.0), 4)
        for litellm_key in user_data.get("keys", []):
            db_key_id = _find_db_key_id_for_litellm_key(
                db=db, region_id=region_id, litellm_key=litellm_key, fallback_team_id=target_user.team_id
            )
            items.append(
                SpendKeyItem(
                    key_id=db_key_id or -1,
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
                )
            )
    except Exception:
        keys = (
            db.query(DBPrivateAIKey)
            .filter(DBPrivateAIKey.region_id == region_id, DBPrivateAIKey.owner_id == user_id)
            .all()
        )
        items, total_spend, _ = await _get_key_spend_items(keys, region)

    return UserSpendResponse(
        region_id=region_id,
        region_name=region.name,
        user_id=user_id,
        team_id=target_user.team_id,
        team_name=target_user.team.name if target_user.team else None,
        total_spend=total_spend,
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
        raise HTTPException(status_code=404, detail="Private AI Key not found in region")

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
    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    try:
        if key.owner_id is not None:
            user_data = await service.get_user_info(str(key.owner_id))
            for litellm_key in user_data.get("keys", []):
                metadata = litellm_key.get("metadata") or {}
                if metadata.get("amazeeai_private_ai_key_name") == key.name:
                    return PrivateAIKeySpend.model_validate(
                        {
                            "spend": litellm_key.get("spend", 0.0),
                            "expires": litellm_key.get("expires"),
                            "created_at": litellm_key.get("created_at"),
                            "updated_at": litellm_key.get("updated_at"),
                            "max_budget": litellm_key.get("max_budget"),
                            "budget_duration": litellm_key.get("budget_duration"),
                            "budget_reset_at": litellm_key.get("budget_reset_at"),
                        }
                    )

        data = await service.get_key_info(key.litellm_token)
        info = data.get("info", {})
        return PrivateAIKeySpend.model_validate({"spend": info.get("spend", 0.0), **info})
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Private AI Key spend: {str(exc)}",
        )
