from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, contains_eager

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from typing import List, Optional
from collections import defaultdict

from app.core.config import settings
from app.core.litellm_user_sync import (
    get_target_regions_for_user,
    sync_add_user_to_team,
    sync_create_user_across_regions,
    sync_delete_user_across_regions,
    sync_remove_user_from_team,
    sync_update_user_team_role,
)
from app.core.limit_service import LimitService
from app.db.database import get_db
from app.core.dependencies import get_limit_service
from app.schemas.models import (
    User,
    UserUpdate,
    UserCreate,
    TeamOperation,
    UserAdminRegionResponse,
    UserRoleUpdate,
    UserSpendRegion,
    UserSpendByEmailResponse,
    UserSpendTeam,
    UserMarketingUpdatesByEmailUpdate,
)
from app.db.models import (
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBTeamRegion,
    DBUser,
    DBUserAdminRegion,
    DBUserSpendCache,
)
from app.core.security import (
    get_password_hash,
    get_role_min_system_admin,
    get_current_user_from_auth,
    get_role_min_team_admin,
)
from app.core.email import normalize_email_for_lookup
from app.core.roles import UserRole
from app.services.litellm import LiteLLMService
from app.services.hubspot import HubSpotService
from datetime import datetime, UTC
import logging
import asyncio
import httpx

logger = logging.getLogger(__name__)
_USER_SPEND_CACHE_TTL_SECONDS = 15 * 60
_USER_SPEND_TIMEOUT_SECONDS = 10.0
_USER_SPEND_SEMAPHORE = asyncio.Semaphore(10)


def get_user_by_email(db: Session, email: str) -> Optional[DBUser]:
    """
    Get a user by email (case-insensitive).
    """
    return db.query(DBUser).filter(func.lower(DBUser.email) == email.lower()).first()


router = APIRouter(tags=["users"])


def invalidate_user_spend_cache(db: Session, email: str) -> None:
    """Delete the cached /users/spend response for *email*.

    Call this whenever a write operation (budget set/clear) changes data that
    the cache stores, so the next GET returns fresh values instead of the
    15-minute stale snapshot.

    This helper intentionally does not commit; callers control the transaction
    boundary so cache invalidation remains atomic with the related write.
    """
    normalized = normalize_email_for_lookup(email)
    db.query(DBUserSpendCache).filter(
        DBUserSpendCache.normalized_email == normalized
    ).delete(synchronize_session=False)
    db.flush()


def invalidate_users_spend_cache_bulk(db: Session, emails: list[str]) -> None:
    """Delete cached /users/spend entries for all *emails* in a single query.

    Prefer this over calling invalidate_user_spend_cache in a loop when
    invalidating many users at once (e.g. all members of a team).

    This helper intentionally does not commit; callers control the transaction
    boundary so cache invalidation remains atomic with the related write.
    """
    if not emails:
        return
    normalized_emails = [normalize_email_for_lookup(e) for e in emails]
    db.query(DBUserSpendCache).filter(
        DBUserSpendCache.normalized_email.in_(normalized_emails)
    ).delete(synchronize_session=False)
    db.flush()


def _is_valid_email_input(email: str) -> bool:
    at_idx = email.find("@")
    if at_idx <= 0:
        return False
    domain = email[at_idx + 1 :]
    if not domain or "@" in domain:
        return False
    dot_idx = domain.rfind(".")
    return 0 < dot_idx < len(domain) - 1


def _is_litellm_404(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        detail = str(exc.detail)
        return "Status 404" in detail or "404" in detail
    return False


def _is_litellm_unavailable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.RequestError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, HTTPException):
        detail = str(exc.detail)
        return any(f"Status {code}" in detail for code in (500, 502, 503, 504))
    return False


def _get_user_spend_cache(
    db: Session, normalized_email: str
) -> Optional[DBUserSpendCache]:
    now = datetime.now(UTC)
    return (
        db.query(DBUserSpendCache)
        .filter(
            DBUserSpendCache.normalized_email == normalized_email,
            DBUserSpendCache.expires_at > now,
        )
        .first()
    )


def _upsert_user_spend_cache(
    db: Session, normalized_email: str, response_data: dict, cached_at: datetime
) -> None:
    expires_at = datetime.fromtimestamp(
        cached_at.timestamp() + _USER_SPEND_CACHE_TTL_SECONDS, tz=UTC
    )
    stmt = (
        pg_insert(DBUserSpendCache)
        .values(
            normalized_email=normalized_email,
            response_data=response_data,
            cached_at=cached_at,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=["normalized_email"],
            set_=dict(
                response_data=response_data,
                cached_at=cached_at,
                expires_at=expires_at,
            ),
        )
    )
    db.execute(stmt)
    db.commit()


async def _fetch_region_spend(
    team_id: int,
    team_name: str,
    region: DBRegion,
    user_ids: set[int],
    user_emails: set[str],
    max_budget: float | None = None,
) -> Optional[UserSpendRegion]:
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )

    async with _USER_SPEND_SEMAPHORE:
        try:
            team_info = await asyncio.wait_for(
                service.get_team_info(lite_team_id),
                timeout=_USER_SPEND_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if _is_litellm_404(exc):
                return None
            if _is_litellm_unavailable(exc):
                logger.warning(
                    "LiteLLM unavailable for team %s (%s) in region %s: %s",
                    team_id,
                    team_name,
                    region.name,
                    str(exc),
                )
                return UserSpendRegion(
                    region_id=region.id,
                    region_name=region.name,
                    spend=0.0,
                    status="unavailable",
                    max_budget=max_budget,
                )
            raise

    keys = team_info.get("keys", []) if isinstance(team_info, dict) else []
    spend = 0.0
    user_id_strs = {str(uid) for uid in user_ids}
    for key in keys:
        if not isinstance(key, dict):
            continue
        metadata = key.get("metadata") or {}
        meta_user_id = metadata.get("amazeeai_user_id")
        service_account_id = metadata.get("service_account_id")
        matches = False
        if meta_user_id is not None:
            matches = str(meta_user_id) in user_id_strs
        if not matches and service_account_id:
            matches = str(service_account_id).lower() in user_emails
        if matches:
            try:
                spend += float(key.get("spend") or 0.0)
            except (TypeError, ValueError):
                continue

    return UserSpendRegion(
        region_id=region.id,
        region_name=region.name,
        spend=spend,
        status="ok",
        max_budget=max_budget,
    )


async def _compute_user_spend(
    normalized_email: str, db: Session
) -> tuple[UserSpendByEmailResponse, bool]:
    users = (
        db.query(DBUser, DBTeam.name.label("team_name"))
        .join(DBTeam, DBUser.team_id == DBTeam.id)
        .filter(
            func.regexp_replace(func.lower(DBUser.email), r"\+[^@]*@", "@")
            == normalized_email,
            DBUser.is_active.is_(True),
            DBTeam.deleted_at.is_(None),
            DBUser.team_id.isnot(None),
        )
        .all()
    )
    if not users:
        raise HTTPException(status_code=404, detail="No users found for the email")

    user_ids_by_team: dict[int, set[int]] = defaultdict(set)
    user_emails_by_team: dict[int, set[str]] = defaultdict(set)
    team_names: dict[int, str] = {}
    for user, team_name in users:
        user_ids_by_team[user.team_id].add(user.id)
        user_emails_by_team[user.team_id].add(user.email.lower())
        team_names[user.team_id] = team_name

    team_ids = list(user_ids_by_team.keys())
    # /users/spend returns a team+region projection for the target member.
    # Team-member caps are resolved strictly from spend_caps rows scoped to
    # (team_id, user_id, region_id). If no row exists, max_budget stays null.
    #
    # Normalized email lookup may theoretically return multiple users in the same team;
    # we pick one deterministic member id per team for cap lookup to keep response shape
    # unchanged (one team aggregate entry).
    member_id_by_team = {
        team_id: min(uids) for team_id, uids in user_ids_by_team.items()
    }
    member_caps = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.team_id.in_(team_ids),
            DBSpendCap.user_id.in_(list(member_id_by_team.values())),
            DBSpendCap.max_budget.isnot(None),
        )
        .all()
    )
    max_budget_by_team_region: dict[tuple[int, int], float] = {}
    for cap in member_caps:
        if cap.team_id is None or cap.region_id is None or cap.user_id is None:
            continue
        if member_id_by_team.get(cap.team_id) != cap.user_id:
            continue
        try:
            max_budget_by_team_region[(cap.team_id, cap.region_id)] = float(
                cap.max_budget
            )
        except (TypeError, ValueError):
            continue

    team_regions = (
        db.query(DBTeamRegion, DBRegion)
        .join(DBRegion, DBTeamRegion.region_id == DBRegion.id)
        .filter(DBTeamRegion.team_id.in_(team_ids), DBRegion.is_active.is_(True))
        .all()
    )

    regions_by_team: dict[int, dict[int, DBRegion]] = {
        team_id: {} for team_id in team_ids
    }
    for assoc, region in team_regions:
        regions_by_team.setdefault(assoc.team_id, {})[region.id] = region

    tasks = []
    task_meta: list[tuple[int, str, int]] = []
    region_failures = False

    key_pair_rows = (
        db.query(
            DBPrivateAIKey.team_id, DBPrivateAIKey.owner_id, DBPrivateAIKey.region_id
        )
        .filter(
            or_(
                DBPrivateAIKey.team_id.in_(team_ids),
                DBPrivateAIKey.owner_id.in_(set().union(*user_ids_by_team.values())),
            )
        )
        .distinct()
        .all()
    )
    key_team_region_set = {(row.team_id, row.region_id) for row in key_pair_rows}
    # Per-team set of regions with user-owned keys (team_id=NULL, owner in that team).
    user_key_regions_by_team: dict[int, set[int]] = defaultdict(set)
    for row in key_pair_rows:
        if row.team_id is None and row.owner_id is not None:
            for tid, uids in user_ids_by_team.items():
                if row.owner_id in uids:
                    user_key_regions_by_team[tid].add(row.region_id)

    for team_id in team_ids:
        team_name = team_names[team_id]
        for region in regions_by_team.get(team_id, {}).values():
            if (
                (team_id, region.id) not in key_team_region_set
                and region.id not in user_key_regions_by_team.get(team_id, set())
            ):
                continue

            tasks.append(
                _fetch_region_spend(
                    team_id=team_id,
                    team_name=team_name,
                    region=region,
                    user_ids=user_ids_by_team[team_id],
                    user_emails=user_emails_by_team[team_id],
                    max_budget=max_budget_by_team_region.get((team_id, region.id)),
                )
            )
            task_meta.append((team_id, team_name, region.id))

    team_region_results: dict[int, list[UserSpendRegion]] = defaultdict(list)
    if tasks:
        fetch_results = await asyncio.gather(*tasks)
        for idx, region_result in enumerate(fetch_results):
            team_id, _, _ = task_meta[idx]
            if region_result is None:
                continue
            if region_result.status == "unavailable":
                region_failures = True
            team_region_results[team_id].append(region_result)

    teams: List[UserSpendTeam] = []
    total_spend = 0.0
    for team_id in team_ids:
        regions = sorted(
            team_region_results.get(team_id, []),
            key=lambda r: (r.region_name, r.region_id),
        )
        team_spend = sum(r.spend for r in regions)
        total_spend += team_spend
        teams.append(
            UserSpendTeam(
                team_id=team_id,
                team_name=team_names[team_id],
                spend=team_spend,
                regions=regions,
            )
        )

    response = UserSpendByEmailResponse(
        email=normalized_email,
        total_spend=total_spend,
        teams=sorted(teams, key=lambda t: t.team_id),
        cached_at=datetime.now(UTC),
    )
    return response, region_failures


def _create_default_limits_for_user(user: DBUser, db: Session) -> None:
    """
    Create default limits for a newly created user.

    Args:
        user: The user to create limits for
        db: Database session
    """
    if settings.ENABLE_LIMITS:
        try:
            limit_service = LimitService(db)
            limit_service.set_user_limits(user)
            logger.info(f"Created default limits for user {user.id} ({user.email})")
        except Exception as e:
            logger.error(
                f"Failed to create default limits for user {user.id}: {str(e)}"
            )
            # Don't fail user creation if limit creation fails


@router.get(
    "/search",
    response_model=List[User],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def search_users(
    email: str = Query(
        ...,
        min_length=1,
        description="Partial email string to match against (case-insensitive substring match)",
        example="alice@example",
    ),
    db: Session = Depends(get_db),
):
    """
    Search users by email pattern. Only accessible by admin users.
    Returns a list of users whose email matches the search pattern.
    Only returns active users from non-deleted teams.
    """
    users = (
        db.query(DBUser)
        .outerjoin(DBTeam, DBUser.team_id == DBTeam.id)
        .filter(
            DBUser.email.ilike(f"%{email}%"),
            DBUser.is_active.is_(True),
            (DBUser.team_id.is_(None)) | (DBTeam.deleted_at.is_(None)),
        )
        .limit(10)
        .all()
    )
    return users


@router.get(
    "/by-email",
    response_model=List[User],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_users_by_email(
    email: str = Query(
        ...,
        description="Exact base email to look up; any +suffix is stripped before matching",
        example="alice@example.com",
        pattern=r"^[^@]+@[^@]+\.[^@]+$",
    ),
    db: Session = Depends(get_db),
):
    """
    Look up users whose base email (with any +suffix stripped) matches the supplied email.
    Accessible by system admins only.
    Returns an empty list when no matches are found.
    Inactive users and users belonging to soft-deleted teams are excluded.
    """
    parts = email.lower().rsplit("@", 1)
    if len(parts) == 2:
        local_part = parts[0].split("+")[0]
        normalized_email = f"{local_part}@{parts[1]}"
    else:
        normalized_email = email.lower()

    rows = (
        db.query(DBUser, DBTeam.name.label("team_name"))
        .outerjoin(DBTeam, DBUser.team_id == DBTeam.id)
        .filter(
            func.regexp_replace(func.lower(DBUser.email), r"\+[^@]*@", "@")
            == normalized_email,
            DBUser.is_active.is_(True),
            (DBUser.team_id.is_(None)) | (DBTeam.deleted_at.is_(None)),
        )
        .all()
    )

    result = []
    for user, team_name in rows:
        user.team_name = team_name
        result.append(user)
    return result


@router.put(
    "/by-email/marketing-updates",
    response_model=List[User],
    dependencies=[Depends(get_role_min_system_admin)],
)
async def update_users_marketing_updates_by_email(
    payload: UserMarketingUpdatesByEmailUpdate,
    db: Session = Depends(get_db),
):
    normalized_email = _normalize_email_for_lookup(payload.email)

    users = (
        db.query(DBUser)
        .outerjoin(DBTeam, DBUser.team_id == DBTeam.id)
        .filter(
            func.regexp_replace(func.lower(DBUser.email), r"\+[^@]*@", "@")
            == normalized_email,
            DBUser.is_active.is_(True),
            (DBUser.team_id.is_(None)) | (DBTeam.deleted_at.is_(None)),
        )
        .all()
    )
    if not users:
        return []

    for user in users:
        user.receive_marketing_updates = payload.receive_marketing_updates
    db.commit()
    for user in users:
        db.refresh(user)

    hubspot = HubSpotService()
    try:
        await hubspot.upsert_contacts_marketable_status(
            [(normalized_email, user.receive_marketing_updates) for user in users]
        )
    except HTTPException:
        logger.exception("HubSpot sync failed for users marketing-updates by email")

    return users


@router.get(
    "/spend",
    response_model=UserSpendByEmailResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def get_user_spend(
    email: Optional[str] = Query(
        None,
        description="Exact base email to look up; any +suffix is stripped before matching",
        example="alice@example.com",
    ),
    db: Session = Depends(get_db),
):
    if not email or not _is_valid_email_input(email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid email",
        )
    normalized_email = normalize_email_for_lookup(email)

    cached = _get_user_spend_cache(db, normalized_email)
    if cached:
        payload = dict(cached.response_data or {})
        payload["cached_at"] = cached.cached_at
        return UserSpendByEmailResponse.model_validate(payload)

    response, had_region_failures = await _compute_user_spend(normalized_email, db)
    if not had_region_failures:
        payload = response.model_dump(mode="json")
        payload.pop("cached_at", None)
        _upsert_user_spend_cache(db, normalized_email, payload, response.cached_at)
    return response


@router.get(
    "/{user_id}/admin-regions",
    response_model=List[UserAdminRegionResponse],
)
async def list_user_admin_regions(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    if not current_user.is_admin and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return (
        db.query(DBUserAdminRegion)
        .join(DBRegion, DBUserAdminRegion.region_id == DBRegion.id)
        .filter(DBUserAdminRegion.user_id == user_id, DBRegion.is_dedicated.is_(True))
        .options(contains_eager(DBUserAdminRegion.region))
        .all()
    )


@router.post(
    "/{user_id}/admin-regions/{region_id}",
    response_model=UserAdminRegionResponse,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def assign_user_admin_region(
    user_id: int,
    region_id: int,
    db: Session = Depends(get_db),
):
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    if not region.is_dedicated:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only dedicated regions can be assigned as admin regions",
        )

    association = (
        db.query(DBUserAdminRegion)
        .filter(
            DBUserAdminRegion.user_id == user_id,
            DBUserAdminRegion.region_id == region_id,
        )
        .first()
    )
    if association:
        return association

    association = DBUserAdminRegion(user_id=user_id, region_id=region_id)
    db.add(association)
    db.commit()
    db.refresh(association)
    return association


@router.delete(
    "/{user_id}/admin-regions/{region_id}",
    dependencies=[Depends(get_role_min_system_admin)],
)
async def remove_user_admin_region(
    user_id: int,
    region_id: int,
    db: Session = Depends(get_db),
):
    association = (
        db.query(DBUserAdminRegion)
        .filter(
            DBUserAdminRegion.user_id == user_id,
            DBUserAdminRegion.region_id == region_id,
        )
        .first()
    )
    if not association:
        raise HTTPException(
            status_code=404, detail="User admin-region association not found"
        )

    db.delete(association)
    db.commit()
    return {"message": "User admin-region association removed"}


@router.get(
    "", response_model=List[User], dependencies=[Depends(get_role_min_team_admin)]
)
@router.get(
    "/", response_model=List[User], dependencies=[Depends(get_role_min_team_admin)]
)
async def list_users(
    current_user: DBUser = Depends(get_current_user_from_auth),
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    List users. Accessible by admin users or team admins for their team members.
    Users from soft-deleted teams and inactive users are excluded from the results.
    Optionally filter by search term (partial email match).
    """
    if current_user.is_admin:
        # Use LEFT JOIN to get all users and their team information in a single query
        # Exclude users from soft-deleted teams and inactive users
        query = (
            db.query(DBUser, DBTeam.name.label("team_name"))
            .outerjoin(DBTeam, DBUser.team_id == DBTeam.id)
            .filter(
                DBUser.is_active.is_(True),
                (DBUser.team_id.is_(None)) | (DBTeam.deleted_at.is_(None)),
            )
        )

        if search:
            query = query.filter(DBUser.email.ilike(f"%{search}%"))

        users = query.all()
    else:
        # Return only users in the team admin's team with team information
        # Exclude if team is soft-deleted or user is inactive
        query = (
            db.query(DBUser, DBTeam.name.label("team_name"))
            .join(DBTeam, DBUser.team_id == DBTeam.id)
            .filter(
                DBUser.team_id == current_user.team_id,
                DBUser.is_active.is_(True),
                DBTeam.deleted_at.is_(None),
            )
        )

        if search:
            query = query.filter(DBUser.email.ilike(f"%{search}%"))

        users = query.all()

    # Map the results to DBUser objects with team_name
    result = []
    for user, team_name in users:
        user.team_name = team_name
        result.append(user)
    return result


@router.post(
    "",
    response_model=User,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_team_admin)],
)
@router.post(
    "/",
    response_model=User,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_role_min_team_admin)],
)
async def create_user(
    user: UserCreate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    """
    Create a new user. Accessible by admin users or team admins for their own team.
    """
    # Check if email already exists
    db_user = get_user_by_email(db, user.email)
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    # Team admin may only create a user in their own team, system admin may create in any team
    if not current_user.is_admin and current_user.team_id != user.team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to perform this action",
        )

    return await _create_user_in_db(user, db)


async def _create_user_in_db(user: UserCreate, db: Session) -> DBUser:
    limit_service = get_limit_service(db)
    if settings.ENABLE_LIMITS and user.team_id is not None:
        limit_service.check_team_user_limit(user.team_id)

    # Validate role if provided
    if user.role and user.role not in UserRole.get_all_roles():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(UserRole.get_all_roles())}",
        )

    # Default to the lowest permissions for a user in a team
    if user.role is None and user.team_id is not None:
        user.role = "read_only"

    # Create the user
    if user.password:
        hashed_password = get_password_hash(user.password)
    else:
        hashed_password = None
    db_user = DBUser(
        email=user.email,
        hashed_password=hashed_password,
        is_admin=False,  # Users are created as non-admin by default
        team_id=user.team_id,
        role=user.role,
        receive_marketing_updates=(
            user.receive_marketing_updates
            if user.receive_marketing_updates is not None
            else False
        ),
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    try:
        await sync_create_user_across_regions(
            db=db, db_user=db_user, team_id=user.team_id
        )
    except Exception:
        # Compensating action to preserve strong consistency semantics.
        db.delete(db_user)
        db.commit()
        raise

    # Create default limits for the user
    _create_default_limits_for_user(db_user, db)

    return db_user


@router.get("/{user_id}", response_model=User)
async def get_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()

    # If user doesn't exist, return 404
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Allow admin users to view any user
    if current_user.is_admin:
        return db_user

    # Allow team admins to view users in their own team
    if (
        current_user.team_id is not None
        and current_user.team_id == db_user.team_id
        and current_user.role == "admin"
    ):
        return db_user

    # Otherwise, return 404 to avoid leaking information about user existence
    raise HTTPException(status_code=404, detail="User not found")


@router.put(
    "/{user_id}", response_model=User, dependencies=[Depends(get_role_min_team_admin)]
)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    """
    Update a user. Accessible by admin users or team admins.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user or (
        db_user.team_id != current_user.team_id and not current_user.is_admin
    ):
        raise HTTPException(status_code=404, detail="User not found")

    # Check if trying to make a team member an admin
    if user_update.is_admin is True:
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action",
            )
        elif db_user.team_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Team members cannot be made administrators",
            )

    previous_email = db_user.email
    previous_marketing_updates = db_user.receive_marketing_updates
    for key, value in user_update.model_dump(exclude_unset=True).items():
        setattr(db_user, key, value)

    synced_regions = []
    updated_regions = []
    if user_update.email is not None:
        synced_regions = get_target_regions_for_user(db, db_user.team_id)
        for region in synced_regions:
            service = LiteLLMService(
                api_url=region.litellm_api_url, api_key=region.litellm_api_key
            )
            try:
                await service.update_user(
                    user_id=str(db_user.id),
                    updates={"user_email": db_user.email},
                )
                updated_regions.append(region)
            except Exception:
                logger.exception(
                    "Failed to sync LiteLLM user email update for user_id=%s region_id=%s",
                    db_user.id,
                    region.id,
                )
                for updated_region in updated_regions:
                    try:
                        rollback_service = LiteLLMService(
                            api_url=updated_region.litellm_api_url,
                            api_key=updated_region.litellm_api_key,
                        )
                        await rollback_service.update_user(
                            user_id=str(db_user.id),
                            updates={"user_email": previous_email},
                        )
                    except Exception as rollback_exc:
                        logger.error(
                            "Failed to rollback LiteLLM user email for user_id=%s region_id=%s: %s",
                            db_user.id,
                            updated_region.id,
                            str(rollback_exc),
                        )
                db.rollback()
                raise

    try:
        db.commit()
    except Exception:
        db.rollback()
        # Best-effort rollback for remote side effects when DB commit fails.
        if user_update.email is not None and previous_email != db_user.email:
            for region in updated_regions:
                try:
                    service = LiteLLMService(
                        api_url=region.litellm_api_url, api_key=region.litellm_api_key
                    )
                    await service.update_user(
                        user_id=str(db_user.id),
                        updates={"user_email": previous_email},
                    )
                except Exception as rollback_exc:
                    logger.error(
                        "Failed to rollback LiteLLM user email for user_id=%s region_id=%s: %s",
                        db_user.id,
                        region.id,
                        str(rollback_exc),
                    )
        raise

    db.refresh(db_user)

    if (
        user_update.receive_marketing_updates is not None
        and user_update.receive_marketing_updates != previous_marketing_updates
    ):
        hubspot = HubSpotService()
        try:
            await hubspot.upsert_contact_marketable_status(
                email=db_user.email, enabled=db_user.receive_marketing_updates
            )
        except HTTPException:
            logger.exception(
                "HubSpot sync failed for user marketing-updates update user_id=%s",
                db_user.id,
            )

    return db_user


@router.post(
    "/{user_id}/add-to-team",
    response_model=User,
    dependencies=[Depends(get_role_min_team_admin)],
)
async def add_user_to_team(
    user_id: int, team_operation: TeamOperation, db: Session = Depends(get_db)
):
    """
    Add a user to a team. Accessible by admin users or team admins.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is already a member of another team
    if db_user.team_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of another team",
        )

    # Check if trying to add an admin to a team
    if db_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot be added to teams",
        )

    # Check if team exists
    db_team = db.query(DBTeam).filter(DBTeam.id == team_operation.team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Add user to team
    db_user.team_id = team_operation.team_id
    try:
        db.commit()
        db.refresh(db_user)
    except Exception:
        db.rollback()
        raise

    try:
        await sync_add_user_to_team(db=db, db_user=db_user, team_id=db_team.id)
    except Exception:
        try:
            db_user.team_id = None
            db.commit()
            db.refresh(db_user)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to revert team assignment for user %s after LiteLLM sync failure",
                db_user.id,
            )
        raise
    return db_user


@router.post(
    "/{user_id}/remove-from-team",
    response_model=User,
    dependencies=[Depends(get_role_min_system_admin)],
)
async def remove_user_from_team(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    """
    Remove a user from a team. Accessible by admin users.
    """
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is a member of a team
    if db_user.team_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not a member of any team",
        )

    # Remove user from team
    previous_team_id = db_user.team_id
    db_user.team_id = None
    try:
        db.commit()
        db.refresh(db_user)
    except Exception:
        db.rollback()
        raise

    try:
        await sync_remove_user_from_team(
            db=db, db_user=db_user, team_id=previous_team_id
        )
    except Exception:
        try:
            db_user.team_id = previous_team_id
            db.commit()
            db.refresh(db_user)
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to revert team removal for user %s after LiteLLM sync failure",
                db_user.id,
            )
        raise
    return db_user


@router.delete("/{user_id}", dependencies=[Depends(get_role_min_system_admin)])
async def delete_user(
    user_id: int,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has associated AI keys
    if db_user.private_ai_keys and len(db_user.private_ai_keys) > 0:
        raise HTTPException(
            status_code=400, detail="Cannot delete user with associated AI keys"
        )

    team_id = db_user.team_id
    db.delete(db_user)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    try:
        await sync_delete_user_across_regions(db=db, db_user=db_user, team_id=team_id)
    except Exception:
        logger.exception(
            "User %s deleted from DB but failed to sync deletion across LiteLLM regions",
            user_id,
        )
    return {"message": "User deleted successfully"}


@router.post(
    "/{user_id}/role",
    response_model=User,
    dependencies=[Depends(get_role_min_team_admin)],
)
async def update_user_role(
    user_id: int,
    role_update: UserRoleUpdate,
    current_user: DBUser = Depends(get_current_user_from_auth),
    db: Session = Depends(get_db),
):
    """
    Update a user's role. Accessible by admin users or team admins for their team members.
    """
    # Validate role
    if role_update.role not in UserRole.get_all_roles():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role. Must be one of: {', '.join(UserRole.get_all_roles())}",
        )

    # Get the user to update
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check authorization
    if not current_user.is_admin:
        # If team admin, ensure they're updating a user in their own team
        if db_user.team_id != current_user.team_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to perform this action",
            )

    # Don't allow changing admin roles through this endpoint
    if db_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change role of an administrator",
        )

    # Update the role
    previous_role = db_user.role
    db_user.role = role_update.role
    db_user.updated_at = datetime.now(UTC)
    remote_role_synced = False

    if db_user.team_id is not None:
        try:
            await sync_update_user_team_role(
                db=db, db_user=db_user, team_id=db_user.team_id
            )
            remote_role_synced = True
        except Exception:
            db.rollback()
            raise

    try:
        db.commit()
    except Exception:
        db.rollback()
        if remote_role_synced and db_user.team_id is not None:
            try:
                db_user.role = previous_role
                await sync_update_user_team_role(
                    db=db, db_user=db_user, team_id=db_user.team_id
                )
            except Exception:
                logger.exception(
                    "Failed to revert LiteLLM role sync after DB commit failure",
                    extra={"user_id": user_id, "team_id": db_user.team_id},
                )
        raise

    db.refresh(db_user)
    return db_user
