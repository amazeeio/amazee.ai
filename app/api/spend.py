import logging
import asyncio
import re
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Query
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.limit_service import DEFAULT_MAX_SPEND, LimitService
from app.core.litellm_user_sync import team_role_for_litellm
from app.core.roles import UserRole
from app.core.team_service import get_team_region_litellm_keys
from app.core.periodic_budget_ledger_service import compute_active_topup_remaining
from app.core.security import (
    get_current_user_from_auth,
    get_private_ai_access,
    get_role_min_team_admin,
)
from app.db.database import get_db
from app.db.models import (
    DBPeriodicPayment,
    DBPoolPurchase,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBTeamSpendPeriod,
    DBTeamRegion,
    DBUser,
)
from app.schemas.limits import OwnerType, ResourceType
from app.api.users import invalidate_user_spend_cache
from app.schemas.models import (
    BudgetType,
    PrivateAIKeySpend,
    SpendBudgetUpdateRequest,
    SpendBudgetUpdateResponse,
    SpendKeyItem,
    TeamSpendHistoryKeyItem,
    TeamPeriodicTransactionItem,
    TeamSpendHistoryPeriodItem,
    TeamSpendHistoryResponse,
    TeamSpendResponse,
    PeriodicTeamBudgetView,
    UserSpendResponse,
)
from app.services.litellm import LiteLLMService

router = APIRouter(tags=["spend"])
logger = logging.getLogger(__name__)
MONTHLY_BUDGET_DURATION = "1mo"


def _compute_period_start(
    budget_reset_at: datetime | None, budget_duration: str | None
) -> datetime | None:
    """
    Derive the start of the current budget period from LiteLLM's
    ``budget_reset_at`` (end-of-period) and ``budget_duration``.

    LiteLLM sets ``budget_reset_at`` to the moment the budget will auto-reset.
    For ``"Nd"`` durations the reset is rolling N days after the last update;
    for ``"1mo"`` / ``"30d"`` it snaps to the 1st of the next calendar month.

    We parse the duration string and subtract from ``budget_reset_at`` to get
    a best-effort calendar ``period_start``.  Returns ``None`` when either
    input is missing or the duration cannot be parsed.
    """
    if budget_reset_at is None or not budget_duration:
        return None

    # Handle "1mo" / "30d" — both snap to 1st of next calendar month
    # so the period start is always the 1st of the current month.
    if budget_duration in ("1mo", "30d"):
        # budget_reset_at is midnight on the 1st of next month.
        # If reset is on 1st, the period that just ended started last month.
        if budget_reset_at.day == 1:
            if budget_reset_at.month == 1:
                return budget_reset_at.replace(
                    year=budget_reset_at.year - 1, month=12, day=1
                )
            return budget_reset_at.replace(month=budget_reset_at.month - 1, day=1)
        return budget_reset_at.replace(day=1)

    match = re.fullmatch(r"(\d+)([dhms])", budget_duration)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return budget_reset_at - timedelta(days=value)
    if unit == "h":
        return budget_reset_at - timedelta(hours=value)
    if unit == "m":
        return budget_reset_at - timedelta(minutes=value)
    if unit == "s":
        return budget_reset_at - timedelta(seconds=value)
    return None


@router.get(
    "/{region_id}/team/{team_id}/history",
    response_model=TeamSpendHistoryResponse,
    summary="Get historical team spend by region",
    description=(
        "Returns historical spend periods from the API database for a team in a "
        "region, including per-key spend for each period. For PERIODIC teams, "
        "response also includes region-scoped `periodic_transactions` entries "
        "covering Stripe renewals and top-up purchases linked to that region."
    ),
    response_description=(
        "Team historical spend periods with per-key breakdown, plus periodic "
        "transaction history for PERIODIC teams."
    ),
)
async def get_team_spend_history(
    region_id: int,
    team_id: int,
    period_limit: int = Query(default=200, ge=1, le=1000),
    tx_limit: int = Query(default=200, ge=1, le=1000),
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

    periods = (
        db.query(DBTeamSpendPeriod)
        .options(selectinload(DBTeamSpendPeriod.keys))
        .filter(
            DBTeamSpendPeriod.team_id == team_id,
            DBTeamSpendPeriod.region_id == region_id,
        )
        .order_by(DBTeamSpendPeriod.period_end.desc(), DBTeamSpendPeriod.id.desc())
        .limit(period_limit)
        .all()
    )

    period_items: list[TeamSpendHistoryPeriodItem] = []
    for period in periods:
        key_items = [
            TeamSpendHistoryKeyItem(
                key_id=row.key_id,
                owner_id=row.owner_id,
                key_name_snapshot=row.key_name_snapshot,
                spend=round(float(row.spend or 0.0), 4),
                max_budget=(
                    round(float(row.max_budget), 4)
                    if row.max_budget is not None
                    else None
                ),
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                total_tokens=row.total_tokens,
            )
            for row in sorted(period.keys, key=lambda k: k.id)
        ]

        period_items.append(
            TeamSpendHistoryPeriodItem(
                period_start=period.period_start,
                period_end=period.period_end,
                budget_type=period.budget_type,
                total_spend=round(float(period.total_spend or 0.0), 4),
                total_budget=(
                    round(float(period.total_budget), 4)
                    if period.total_budget is not None
                    else None
                ),
                total_prompt_tokens=period.total_prompt_tokens,
                total_completion_tokens=period.total_completion_tokens,
                total_tokens=period.total_tokens,
                source=period.source,
                stripe_event_id=period.stripe_event_id,
                stripe_invoice_id=period.stripe_invoice_id,
                stripe_subscription_id=period.stripe_subscription_id,
                keys=key_items,
            )
        )

    periodic_transactions: list[TeamPeriodicTransactionItem] = []
    if team.budget_type == BudgetType.PERIODIC:
        # Show all periodic purchases for the team (subscription + topup),
        # independent of whether a region-scoped ledger entry has already
        # linked source_payment_id for that payment.
        periodic_rows = (
            db.query(DBPeriodicPayment)
            .filter(DBPeriodicPayment.team_id == team_id)
            .order_by(
                DBPeriodicPayment.payment_date.desc(),
                DBPeriodicPayment.id.desc(),
            )
            .limit(tx_limit)
            .all()
        )
        periodic_transactions = [
            TeamPeriodicTransactionItem(
                id=row.id,
                payment_type=row.payment_type,
                amount_cents=row.amount_cents,
                currency=row.currency,
                stripe_payment_id=row.stripe_payment_id,
                payment_date=row.payment_date,
                status=row.status,
                sync_status=row.sync_status,
                source="periodic_payments",
            )
            for row in periodic_rows
        ]

    return TeamSpendHistoryResponse(
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        team_name=team.name,
        periods=period_items,
        periodic_transactions=periodic_transactions,
    )


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


def _effective_monthly_budget_duration(max_budget: float | None) -> str | None:
    """Use calendar-month windows whenever a budget cap is set."""
    if max_budget is None:
        return None
    return MONTHLY_BUDGET_DURATION


def _effective_team_budget_duration(
    team: DBTeam, max_budget: float | None
) -> str | None:
    """
    Resolve team budget duration by budget model:
    - POOL teams: preserve purchase-expiration window (use-it-or-lose-it lifecycle)
    - PERIODIC teams: enforce calendar-month windows for caps
    """
    if max_budget is None:
        return None
    if team.requires_pool_purchase_gate:
        return f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d"
    return MONTHLY_BUDGET_DURATION


def _current_month_anchor() -> date:
    now = datetime.now(UTC)
    return date(year=now.year, month=now.month, day=1)


def _compute_pool_monthly_effective_budget(
    purchased_total: float,
    month_start_spend: float,
    monthly_cap: float,
) -> float:
    # LiteLLM max_budget is an absolute ceiling in the active 365d window.
    # To allow exactly `monthly_cap` during this month, shift by month_start_spend.
    return round(
        min(float(purchased_total), float(month_start_spend) + float(monthly_cap)), 4
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
    association = (
        db.query(DBTeamRegion)
        .filter(DBTeamRegion.region_id == region.id, DBTeamRegion.team_id == team_id)
        .first()
    )
    if not association:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is not associated with this region",
        )


def _invalidate_team_user_spend_cache(db: Session, team_id: int) -> None:
    team_user_emails = (
        db.query(DBUser.email)
        .filter(DBUser.team_id == team_id, DBUser.is_active.is_(True))
        .all()
    )
    for (email,) in team_user_emails:
        if email:
            invalidate_user_spend_cache(db, email)


def _invalidate_key_related_user_spend_cache(db: Session, key: DBPrivateAIKey) -> None:
    if key.owner_id is not None:
        owner = db.query(DBUser).filter(DBUser.id == key.owner_id).first()
        if owner and owner.email:
            invalidate_user_spend_cache(db, owner.email)
    elif key.team_id is not None:
        _invalidate_team_user_spend_cache(db, key.team_id)


def _pool_purchased_budget_for_team_region(
    db: Session, team_id: int, region_id: int
) -> float:
    total_purchased_cents = (
        db.query(func.sum(DBPoolPurchase.amount_cents))
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .scalar()
        or 0
    )
    return round(float(total_purchased_cents) / 100.0, 4)


def _is_no_purchase_pool_team(team: DBTeam | None, db: Session, region_id: int) -> bool:
    if team is None or not team.requires_pool_purchase_gate:
        return False
    return _pool_purchased_budget_for_team_region(db, team.id, region_id) <= 0


def _get_spend_cap_max_budget(
    db: Session,
    *,
    scope: str,
    region_id: int,
    team_id: int | None = None,
    user_id: int | None = None,
    key_id: int | None = None,
) -> float | None:
    cap = (
        db.query(DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == scope,
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team_id,
            DBSpendCap.user_id == user_id,
            DBSpendCap.key_id == key_id,
        )
        .first()
    )
    if cap is None or cap[0] is None:
        return None
    return float(cap[0])


def _get_key_spend_cap_map(
    db: Session, *, region_id: int, key_ids: list[int]
) -> dict[int, float]:
    if not key_ids:
        return {}
    rows = (
        db.query(DBSpendCap.key_id, DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == region_id,
            DBSpendCap.key_id.in_(key_ids),
            DBSpendCap.max_budget.isnot(None),
        )
        .all()
    )
    return {int(key_id): float(max_budget) for key_id, max_budget in rows}


def _pool_budget_duration_from_last_purchase(
    db: Session, team_id: int, region_id: int
) -> str:
    latest_purchase_at = (
        db.query(func.max(DBPoolPurchase.purchased_at))
        .filter(
            DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
        )
        .scalar()
    )
    if latest_purchase_at is None:
        return f"{settings.POOL_BUDGET_EXPIRATION_DAYS}d"
    if latest_purchase_at.tzinfo is None:
        latest_purchase = latest_purchase_at.replace(tzinfo=UTC)
    else:
        latest_purchase = latest_purchase_at
    days_since_last_purchase = (datetime.now(UTC) - latest_purchase).days
    days_left = max(0, settings.POOL_BUDGET_EXPIRATION_DAYS - days_since_last_purchase)
    return f"{days_left}d"


async def _enforce_pool_no_purchase_key_lock(
    db: Session,
    team: DBTeam | None,
    region: DBRegion,
    service: LiteLLMService,
    key_id: int | None = None,
    user_id: int | None = None,
    purchased_budget: float | None = None,
) -> bool:
    """
    For prepaid-pool teams with no purchased budget in a region, hard-lock
    keys by setting max_budget=0 in LiteLLM to avoid the team budget zero-edge.
    """
    if team is None or not team.requires_pool_purchase_gate:
        return False
    if purchased_budget is None:
        purchased_budget = _pool_purchased_budget_for_team_region(
            db, team.id, region.id
        )
    if purchased_budget > 0:
        return False
    region_keys = get_team_region_litellm_keys(
        db,
        team_id=team.id,
        region_id=region.id,
        key_id=key_id,
        user_id=user_id,
    )
    if not region_keys:
        return False

    semaphore = asyncio.Semaphore(10)

    async def _lock_key(key: DBPrivateAIKey) -> str | None:
        try:
            async with semaphore:
                await service.update_key_budget(
                    litellm_token=key.litellm_token,
                    budget_duration=MONTHLY_BUDGET_DURATION,
                    max_budget=0.0,
                    clear_max_budget=False,
                )
            return None
        except Exception as exc:
            return f"Key {key.id}: {str(exc)}"

    errors = [
        error
        for error in await asyncio.gather(*[_lock_key(key) for key in region_keys])
        if error is not None
    ]
    if errors:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Failed to enforce no-purchase key lock in LiteLLM: "
                + "; ".join(errors)
            ),
        )
    return True


def _upsert_spend_cap(
    db: Session,
    *,
    scope: str,
    region_id: int,
    team_id: int | None = None,
    user_id: int | None = None,
    key_id: int | None = None,
    max_budget: float | None = None,
    budget_duration: str | None = None,
    month_anchor: date | None = None,
    month_start_spend: float | None = None,
) -> None:
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == scope,
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team_id,
            DBSpendCap.user_id == user_id,
            DBSpendCap.key_id == key_id,
        )
        .first()
    )
    if cap is None:
        cap = DBSpendCap(
            scope=scope,
            region_id=region_id,
            team_id=team_id,
            user_id=user_id,
            key_id=key_id,
        )
    cap.max_budget = max_budget
    cap.budget_duration = budget_duration
    cap.month_anchor = month_anchor
    cap.month_start_spend = month_start_spend
    db.add(cap)
    # Defer commit to the endpoint so DB changes and remote sync share one boundary.
    db.flush()


def _delete_spend_cap(
    db: Session,
    *,
    scope: str,
    region_id: int,
    team_id: int | None = None,
    user_id: int | None = None,
    key_id: int | None = None,
) -> None:
    (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == scope,
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team_id,
            DBSpendCap.user_id == user_id,
            DBSpendCap.key_id == key_id,
        )
        .delete()
    )
    # Defer commit to the endpoint so DB changes and remote sync share one boundary.
    db.flush()


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


@router.get(
    "/{region_id}/team/{team_id}",
    response_model=TeamSpendResponse,
    summary="Get team spend by region",
    description=(
        "Returns aggregated spend for a team in a region, including per-key spend, "
        "token usage fields, and effective budget totals."
    ),
    response_description="Team spend summary and per-key breakdown.",
)
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

    is_periodic = not team.requires_pool_purchase_gate
    litellm_fetch_ok = False

    try:
        team_data = await service.get_team_info(lite_team_id)
        team_info = team_data.get("team_info", team_data)
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
            key_spend = round(float(litellm_key.get("spend", 0.0) or 0.0), 4)
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
                    spend=key_spend,
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
                    budget_duration=litellm_key.get("budget_duration"),
                    budget_reset_at=(
                        datetime.fromisoformat(litellm_key["budget_reset_at"])
                        if litellm_key.get("budget_reset_at")
                        else None
                    ),
                )
            )

        # For PERIODIC teams, total_spend must reflect only the current
        # billing period. Because the team-level spend counter in LiteLLM
        # is never reset (it compounds), we derive total_spend from the raw
        # per-key spends which ARE reset to 0 on each Stripe webhook, and
        # only round the final aggregate to avoid drift from summing
        # already-rounded display values.
        if is_periodic and items:
            total_spend = round(
                sum(
                    float(litellm_key.get("spend", 0.0) or 0.0)
                    for litellm_key in team_data.get("keys", [])
                ),
                4,
            )
        else:
            total_spend = round(float(team_info.get("spend", 0.0) or 0.0), 4)
        litellm_fetch_ok = True
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

    configured_team_cap = _get_spend_cap_max_budget(
        db, scope="team", region_id=region_id, team_id=team_id
    )
    if configured_team_cap is not None and team.requires_pool_purchase_gate:
        total_budget = round(configured_team_cap, 4)
    elif is_periodic and litellm_fetch_ok:
        # For PERIODIC teams, display the effective current team budget as seen by
        # LiteLLM (includes active subscription carry + top-ups), not per-key cap.
        max_budget = team_info.get("max_budget")
        if max_budget is not None:
            total_budget = round(float(max_budget or 0.0), 4)
    key_cap_map = _get_key_spend_cap_map(
        db,
        region_id=region_id,
        key_ids=[item.key_id for item in items if item.key_id is not None],
    )
    for item in items:
        if item.key_id is not None and item.key_id in key_cap_map:
            item.max_budget = round(key_cap_map[item.key_id], 4)

    # Compute period_start for each key from budget_reset_at + budget_duration.
    team_budget_duration = None
    team_budget_reset_at = None
    if litellm_fetch_ok:
        team_budget_duration = team_info.get("budget_duration")
        team_budget_reset_at_raw = team_info.get("budget_reset_at")
        if team_budget_reset_at_raw:
            team_budget_reset_at = datetime.fromisoformat(team_budget_reset_at_raw)
    for item in items:
        item.period_start = _compute_period_start(
            item.budget_reset_at, item.budget_duration
        )
    team_period_start = _compute_period_start(
        team_budget_reset_at, team_budget_duration
    )

    periodic_budget_view = None
    if is_periodic:
        now = datetime.now(UTC)
        sub_remaining_cents = 0
        # Use ledger-driven periodic status semantics for user-facing budget numbers.
        from app.db.models import DBPeriodicBudgetLedgerEntry

        sub_rows = (
            db.query(DBPeriodicBudgetLedgerEntry)
            .filter(
                DBPeriodicBudgetLedgerEntry.team_id == team_id,
                DBPeriodicBudgetLedgerEntry.region_id == region_id,
                DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
                DBPeriodicBudgetLedgerEntry.is_active.is_(True),
                (
                    DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                    | (DBPeriodicBudgetLedgerEntry.expires_at > now)
                ),
            )
            .all()
        )
        for row in sub_rows:
            sub_remaining_cents += max(0, row.amount_cents - row.consumed_cents)
        topup_remaining_cents = compute_active_topup_remaining(
            db, team_id=team_id, region_id=region_id
        )
        remaining_cents = sub_remaining_cents + topup_remaining_cents
        periodic_budget_view = PeriodicTeamBudgetView(
            purchased_budget_cents=remaining_cents,
            purchased_budget=round(remaining_cents / 100.0, 4),
            remaining_budget_cents=remaining_cents,
            remaining_budget=round(remaining_cents / 100.0, 4),
            configured_max_budget_cents=int(round(total_budget * 100)),
            configured_max_budget=round(total_budget, 4),
        )

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
        budget_duration=team_budget_duration,
        budget_reset_at=team_budget_reset_at,
        period_start=team_period_start,
        periodic_budget=periodic_budget_view,
        key_count=len(items),
        keys=items,
    )


@router.get(
    "/{region_id}/user/{user_id}",
    response_model=UserSpendResponse,
    summary="Get user spend by region",
    description=(
        "Returns aggregated spend for a user in a region, including per-key spend "
        "and token usage fields."
    ),
    response_description="User spend summary and per-key breakdown.",
)
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
                    budget_duration=litellm_key.get("budget_duration"),
                    budget_reset_at=(
                        datetime.fromisoformat(litellm_key["budget_reset_at"])
                        if litellm_key.get("budget_reset_at")
                        else None
                    ),
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

    member_cap = _get_spend_cap_max_budget(
        db,
        scope="team_member",
        region_id=region_id,
        team_id=target_user.team_id,
        user_id=user_id,
    )
    key_cap_map = _get_key_spend_cap_map(
        db,
        region_id=region_id,
        key_ids=[item.key_id for item in items if item.key_id is not None],
    )
    for item in items:
        if item.key_id is not None and item.key_id in key_cap_map:
            item.max_budget = round(key_cap_map[item.key_id], 4)
        elif member_cap is not None:
            item.max_budget = round(member_cap, 4)
        item.period_start = _compute_period_start(
            item.budget_reset_at, item.budget_duration
        )

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


@router.get(
    "/{region_id}/key/{key_id}",
    response_model=PrivateAIKeySpend,
    summary="Get key spend by region",
    description=(
        "Returns spend and budget metadata for a specific key in the specified region."
    ),
    response_description="Key spend record with budget metadata and token usage.",
)
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
        configured_key_cap = _get_spend_cap_max_budget(
            db,
            scope="key",
            region_id=region_id,
            team_id=key.team_id,
            user_id=key.owner_id,
            key_id=key.id,
        )
        if configured_key_cap is not None:
            info = dict(info)
            info["max_budget"] = round(configured_key_cap, 4)
        budget_reset_at = (
            datetime.fromisoformat(info["budget_reset_at"])
            if info.get("budget_reset_at")
            else None
        )
        period_start = _compute_period_start(
            budget_reset_at, info.get("budget_duration")
        )
        return PrivateAIKeySpend.model_validate(
            {
                "spend": info.get("spend", 0.0),
                **info,
                "budget_reset_at": budget_reset_at,
                "period_start": period_start,
            }
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get Private AI Key spend: {str(exc)}",
        )


@router.put(
    "/{region_id}/team/{team_id}/budget",
    response_model=SpendBudgetUpdateResponse,
    summary="Update team budget",
    description=(
        "**Experimental** endpoint. Contract and behavior may change while team "
        "budget controls are being finalized.\n\n"
        "Request body accepts only `max_budget`.\n"
        "`budget_duration` is computed server-side and returned in the response:\n"
        "- PERIODIC teams: manual team budget updates are rejected; use subscription "
        "renewal and periodic top-up purchase flows.\n"
        "- POOL teams: purchase-window duration for enforcement while storing "
        "monthly-cap semantics in local spend caps."
    ),
    response_description=(
        "Updated team budget state including effective max_budget and server-derived "
        "budget_duration."
    ),
    openapi_extra={"x-experimental": True},
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
    if team.budget_type == BudgetType.PERIODIC and body.max_budget is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Manual team budget updates are not allowed for periodic teams. "
                "Use subscription renewal and periodic top-up purchase flows."
            ),
        )
    if team.requires_pool_purchase_gate and body.max_budget is not None:
        effective_duration = _pool_budget_duration_from_last_purchase(
            db=db, team_id=team_id, region_id=region_id
        )
    else:
        effective_duration = _effective_team_budget_duration(team, body.max_budget)
    effective_max_budget = body.max_budget
    month_anchor = None
    month_start_spend = None
    purchased_total: float | None = None

    if team.requires_pool_purchase_gate and body.max_budget is not None:
        purchased_total = _pool_purchased_budget_for_team_region(db, team_id, region_id)
        team_info_resp = await service.get_team_info(lite_team_id)
        team_info = team_info_resp.get("team_info", team_info_resp)
        month_start_spend = round(float(team_info.get("spend", 0.0) or 0.0), 4)
        month_anchor = _current_month_anchor()
        effective_max_budget = _compute_pool_monthly_effective_budget(
            purchased_total=purchased_total,
            month_start_spend=month_start_spend,
            monthly_cap=body.max_budget,
        )

    await service.update_team_budget(
        team_id=lite_team_id,
        max_budget=effective_max_budget,
        budget_duration=effective_duration,
    )
    if (
        body.max_budget is not None
        and team.requires_pool_purchase_gate
        and purchased_total is not None
        and purchased_total <= 0
    ):
        await _enforce_pool_no_purchase_key_lock(
            db,
            team,
            region,
            service,
            purchased_budget=purchased_total,
        )
    _upsert_spend_cap(
        db,
        scope="team",
        region_id=region_id,
        team_id=team_id,
        max_budget=body.max_budget,
        budget_duration=(
            MONTHLY_BUDGET_DURATION
            if team.requires_pool_purchase_gate
            else effective_duration
        ),
        month_anchor=month_anchor,
        month_start_spend=month_start_spend,
    )
    _invalidate_team_user_spend_cache(db, team_id)
    configured_team_cap = _get_spend_cap_max_budget(
        db, scope="team", region_id=region_id, team_id=team_id
    )
    info = await service.get_team_info(lite_team_id)
    db.commit()
    team_info = info.get("team_info", info)
    return SpendBudgetUpdateResponse(
        scope="team",
        source_endpoint="/team/update",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        max_budget=(
            round(configured_team_cap, 4)
            if configured_team_cap is not None
            else team_info.get("max_budget")
        ),
        budget_duration=team_info.get("budget_duration"),
        note=(
            "For team keys, team budget governs spend enforcement."
            if not team.requires_pool_purchase_gate
            else "POOL monthly cap stored as monthly delta; LiteLLM max_budget is set to month_start_spend + monthly_cap (bounded by purchased total)."
        ),
    )


@router.put(
    "/{region_id}/team/{team_id}/member/{user_id}/budget",
    response_model=SpendBudgetUpdateResponse,
    summary="Update team-member budget",
    description=(
        "Updates a team-scoped per-member budget (`max_budget_in_team`) for the "
        "specified user.\n\n"
        "Request body accepts only `max_budget`.\n"
        "`budget_duration` is derived server-side and returned in the response "
        "(monthly `1mo` when set)."
    ),
    response_description="Updated team-member budget state.",
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

    effective_duration = _effective_monthly_budget_duration(body.max_budget)
    await service.update_team_member(
        team_id=lite_team_id,
        user_id=str(user_id),
        role=team_role_for_litellm(user),
        max_budget_in_team=body.max_budget,
        budget_duration=effective_duration,
    )
    await _enforce_pool_no_purchase_key_lock(db, team, region, service, user_id=user_id)
    _upsert_spend_cap(
        db,
        scope="team_member",
        region_id=region_id,
        team_id=team_id,
        user_id=user_id,
        max_budget=body.max_budget,
        budget_duration=effective_duration,
    )
    invalidate_user_spend_cache(db, user.email)
    db.commit()
    return SpendBudgetUpdateResponse(
        scope="team_member",
        source_endpoint="/team/member_update",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        user_id=user_id,
        max_budget=body.max_budget,
        budget_duration=effective_duration,
        note="This budget is scoped to the user within the specified team.",
    )


@router.post(
    "/{region_id}/team/{team_id}/budget/clear",
    response_model=SpendBudgetUpdateResponse,
    summary="Clear team budget override",
    description=(
        "Clears ad-hoc team budget overrides and restores canonical team budget. "
        "For POOL teams this restores purchased total for the region. "
        "For PERIODIC teams this restores effective policy budget (MANUAL -> PRODUCT -> DEFAULT)."
    ),
    response_description="Team budget clear result with restored max_budget.",
    openapi_extra={
        "responses": {
            200: {
                "content": {
                    "application/json": {
                        "example": {
                            "scope": "team",
                            "source_endpoint": "/team/clear",
                            "region_id": 1,
                            "region_name": "eu-central",
                            "team_id": 42,
                            "max_budget": 50.0,
                            "budget_duration": "365d",
                            "note": "POOL teams restore to purchased total for the region; PERIODIC teams restore to effective policy budget.",
                        }
                    }
                }
            }
        }
    },
)
async def clear_team_budget(
    region_id: int,
    team_id: int,
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

    if team.requires_pool_purchase_gate:
        total_purchased_cents = (
            db.query(func.sum(DBPoolPurchase.amount_cents))
            .filter(
                DBPoolPurchase.team_id == team_id, DBPoolPurchase.region_id == region_id
            )
            .scalar()
            or 0
        )
        max_budget_to_restore = round(float(total_purchased_cents) / 100.0, 4)
    else:
        limit_service = LimitService(db)
        try:
            limit_service.reset_limit(
                owner_type=OwnerType.TEAM,
                owner_id=team_id,
                resource_type=ResourceType.BUDGET,
            )
        except Exception:
            # No persisted team budget limit to reset; use effective fallback budget.
            pass
        _, max_budget_to_restore, _ = limit_service.get_token_restrictions(team_id)
        max_budget_to_restore = round(
            float(max_budget_to_restore or DEFAULT_MAX_SPEND), 4
        )

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team_id)
    if team.requires_pool_purchase_gate and max_budget_to_restore is not None:
        budget_duration = _pool_budget_duration_from_last_purchase(
            db=db, team_id=team_id, region_id=region_id
        )
    else:
        budget_duration = _effective_team_budget_duration(team, max_budget_to_restore)
    await service.update_team_budget(
        team_id=lite_team_id,
        max_budget=max_budget_to_restore,
        budget_duration=budget_duration,
    )
    _delete_spend_cap(db, scope="team", region_id=region_id, team_id=team_id)
    _invalidate_team_user_spend_cache(db, team_id)
    info = await service.get_team_info(lite_team_id)
    db.commit()
    team_info = info.get("team_info", info)
    return SpendBudgetUpdateResponse(
        scope="team",
        source_endpoint="/team/clear",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        max_budget=team_info.get("max_budget"),
        budget_duration=team_info.get("budget_duration"),
        note=(
            "POOL teams restore to purchased total for the region; PERIODIC teams restore to effective policy budget."
        ),
    )


@router.post(
    "/{region_id}/team/{team_id}/member/{user_id}/budget/clear",
    response_model=SpendBudgetUpdateResponse,
    summary="Clear team-member budget override",
    description=(
        "Clears the user's team-scoped budget override (max_budget_in_team) in LiteLLM. "
        "This makes the member fall back to team-level budget behavior."
    ),
    response_description="Team-member budget clear result.",
    openapi_extra={
        "responses": {
            200: {
                "content": {
                    "application/json": {
                        "example": {
                            "scope": "team_member",
                            "source_endpoint": "/team/member_clear",
                            "region_id": 1,
                            "region_name": "eu-central",
                            "team_id": 42,
                            "user_id": 7,
                            "max_budget": None,
                            "budget_duration": None,
                            "note": "Cleared team-member budget override.",
                        }
                    }
                }
            }
        }
    },
)
async def clear_team_member_budget(
    region_id: int,
    team_id: int,
    user_id: int,
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
    await service.update_team_member(
        team_id=lite_team_id,
        user_id=str(user_id),
        role=team_role_for_litellm(user),
        max_budget_in_team=None,
    )
    _delete_spend_cap(
        db, scope="team_member", region_id=region_id, team_id=team_id, user_id=user_id
    )
    invalidate_user_spend_cache(db, user.email)
    db.commit()
    return SpendBudgetUpdateResponse(
        scope="team_member",
        source_endpoint="/team/member_clear",
        region_id=region_id,
        region_name=region.name,
        team_id=team_id,
        user_id=user_id,
        max_budget=None,
        budget_duration=None,
        note="Cleared team-member budget override.",
    )


@router.put(
    "/{region_id}/key/{key_id}/budget",
    response_model=SpendBudgetUpdateResponse,
    summary="Update key budget",
    description=(
        "Updates key-level budget override for the specified key.\n\n"
        "Request body accepts only `max_budget`.\n"
        "`budget_duration` is derived server-side and returned in the response "
        "(monthly `1mo` when set, `null` when clearing max_budget)."
    ),
    response_description="Updated key budget state.",
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
    owner = None
    team_for_budget_check = None
    if key.team_id is not None:
        team_for_budget_check = (
            db.query(DBTeam)
            .filter(DBTeam.id == key.team_id, DBTeam.deleted_at.is_(None))
            .first()
        )
        _assert_team_budget_write_access(current_user, role, key.team_id)
    else:
        owner = db.query(DBUser).filter(DBUser.id == key.owner_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="Key owner not found")
        _assert_user_budget_write_access(current_user, role, owner)
        if owner.team_id is not None:
            team_for_budget_check = (
                db.query(DBTeam)
                .filter(DBTeam.id == owner.team_id, DBTeam.deleted_at.is_(None))
                .first()
            )

    region = _get_region_or_404(db, region_id)
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    effective_duration = _effective_monthly_budget_duration(body.max_budget)
    # For purchase-gated POOL teams with no purchases, skip the initial key
    # budget update (which would be immediately overridden) to avoid a brief
    # unlocked window. The lock call below will hard-set max_budget=0 directly.
    purchased_budget: float | None = None
    skip_initial_update = False
    if (
        team_for_budget_check is not None
        and team_for_budget_check.requires_pool_purchase_gate
        and body.max_budget is not None
    ):
        purchased_budget = _pool_purchased_budget_for_team_region(
            db, team_for_budget_check.id, region_id
        )
        skip_initial_update = purchased_budget <= 0
    if not skip_initial_update:
        await service.update_key_budget(
            litellm_token=key.litellm_token,
            budget_duration=effective_duration,
            max_budget=body.max_budget,
            clear_max_budget=body.max_budget is None,
        )
    lock_applied = await _enforce_pool_no_purchase_key_lock(
        db,
        team_for_budget_check,
        region,
        service,
        key_id=key.id,
        purchased_budget=purchased_budget,
    )
    _upsert_spend_cap(
        db,
        scope="key",
        region_id=region_id,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
        max_budget=body.max_budget,
        budget_duration=effective_duration,
    )
    _invalidate_key_related_user_spend_cache(db, key)
    configured_key_cap = _get_spend_cap_max_budget(
        db,
        scope="key",
        region_id=region_id,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
    )
    key_info = await service.get_key_info(key.litellm_token)
    db.commit()
    info = key_info.get("info", {})
    return SpendBudgetUpdateResponse(
        scope="key",
        source_endpoint="/key/update",
        region_id=region_id,
        region_name=region.name,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
        max_budget=(
            round(configured_key_cap, 4)
            if configured_key_cap is not None
            else info.get("max_budget")
        ),
        budget_duration=info.get("budget_duration"),
        note=(
            "If key has team_id, team/team-member budgets may take precedence during enforcement. "
            "No-purchase prepaid-pool teams keep keys hard-locked at max_budget=0 while preserving configured caps for post-purchase restore."
            if lock_applied
            else "If key has team_id, team/team-member budgets may take precedence during enforcement."
        ),
    )


@router.post(
    "/{region_id}/key/{key_id}/budget/clear",
    response_model=SpendBudgetUpdateResponse,
    summary="Clear key budget override",
    description=(
        "Clears key max_budget and budget_duration by setting both to null "
        "in LiteLLM. Removes the spend cap and budget reset window from the key."
    ),
    response_description="Key budget clear result with max_budget=null and budget_duration=null.",
    openapi_extra={
        "responses": {
            200: {
                "content": {
                    "application/json": {
                        "example": {
                            "scope": "key",
                            "source_endpoint": "/key/clear",
                            "region_id": 1,
                            "region_name": "eu-central",
                            "team_id": 42,
                            "user_id": 7,
                            "key_id": 99,
                            "max_budget": None,
                            "budget_duration": None,
                            "note": "Cleared key max_budget and budget_duration overrides.",
                        }
                    }
                }
            }
        }
    },
)
async def clear_key_budget(
    region_id: int,
    key_id: int,
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
        budget_duration=None,
        max_budget=None,
        clear_max_budget=True,
        clear_budget_duration=True,
    )
    _delete_spend_cap(
        db,
        scope="key",
        region_id=region_id,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
    )
    _invalidate_key_related_user_spend_cache(db, key)
    key_info = await service.get_key_info(key.litellm_token)
    db.commit()
    info = key_info.get("info", {})
    return SpendBudgetUpdateResponse(
        scope="key",
        source_endpoint="/key/clear",
        region_id=region_id,
        region_name=region.name,
        team_id=key.team_id,
        user_id=key.owner_id,
        key_id=key_id,
        max_budget=info.get("max_budget"),
        budget_duration=info.get("budget_duration"),
        note="Cleared key max_budget and budget_duration overrides.",
    )
