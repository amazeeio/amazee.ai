from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    DBPrivateAIKey,
    DBTeam,
    DBTeamSpendPeriod,
    DBTeamSpendPeriodKey,
)
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _resolve_budget_type(team: DBTeam) -> str:
    budget_type = team.budget_type
    if isinstance(budget_type, BudgetType):
        return budget_type.value
    return str(budget_type).lower()


async def fetch_team_spend_snapshot_for_region(
    *,
    db: Session,
    team: DBTeam,
    region,
) -> dict[str, Any]:
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    lite_team_id = LiteLLMService.format_team_id(region.name, team.id)
    team_data = await service.get_team_info(lite_team_id)
    team_info = team_data.get("team_info", team_data)

    # Preload all keys for this region/team once to avoid per-key DB round trips.
    all_region_team_keys: list[DBPrivateAIKey] = (
        db.query(DBPrivateAIKey)
        .filter(
            DBPrivateAIKey.region_id == region.id,
            DBPrivateAIKey.team_id == team.id,
        )
        .order_by(DBPrivateAIKey.id.desc())
        .all()
    )

    keys_payload: list[dict[str, Any]] = []
    for litellm_key in team_data.get("keys", []):
        metadata = litellm_key.get("metadata") or {}
        key_name = metadata.get("amazeeai_private_ai_key_name")
        owner_raw = litellm_key.get("user_id")
        owner_id = int(owner_raw) if str(owner_raw).isdigit() else None

        candidates = list(all_region_team_keys)
        if key_name:
            candidates = [k for k in candidates if k.name == key_name]
        if owner_id is not None:
            candidates = [k for k in candidates if k.owner_id == owner_id]
        db_key = candidates[0] if candidates else None
        key_id = db_key.id if db_key else None

        keys_payload.append(
            {
                "key_id": key_id,
                "owner_id": owner_id,
                "key_name_snapshot": key_name,
                "spend": float(litellm_key.get("spend", 0.0) or 0.0),
                "max_budget": (
                    float(litellm_key.get("max_budget"))
                    if litellm_key.get("max_budget") is not None
                    else None
                ),
                "prompt_tokens": _to_int_or_none(litellm_key.get("prompt_tokens")),
                "completion_tokens": _to_int_or_none(
                    litellm_key.get("completion_tokens")
                ),
                "total_tokens": _to_int_or_none(litellm_key.get("total_tokens")),
            }
        )

    return {
        "total_spend": float(team_info.get("spend", 0.0) or 0.0),
        "total_budget": (
            float(team_info.get("max_budget"))
            if team_info.get("max_budget") is not None
            else None
        ),
        "total_prompt_tokens": int(team_info.get("prompt_tokens"))
        if str(team_info.get("prompt_tokens", "")).isdigit()
        else None,
        "total_completion_tokens": int(team_info.get("completion_tokens"))
        if str(team_info.get("completion_tokens", "")).isdigit()
        else None,
        "total_tokens": int(team_info.get("total_tokens"))
        if str(team_info.get("total_tokens", "")).isdigit()
        else None,
        "keys": keys_payload,
    }


def _query_spend_period(
    db: Session,
    team_id: int,
    region_id: int,
    budget_type: str,
    period_start: datetime,
    period_end: datetime,
):
    return (
        db.query(DBTeamSpendPeriod)
        .filter(
            DBTeamSpendPeriod.team_id == team_id,
            DBTeamSpendPeriod.region_id == region_id,
            DBTeamSpendPeriod.budget_type == budget_type,
            DBTeamSpendPeriod.period_start == period_start,
            DBTeamSpendPeriod.period_end == period_end,
        )
        .first()
    )


def upsert_team_spend_period(
    *,
    db: Session,
    team: DBTeam,
    region_id: int,
    period_start: datetime,
    period_end: datetime,
    source: str,
    snapshot: dict[str, Any],
    stripe_event_id: str | None = None,
    stripe_invoice_id: str | None = None,
    stripe_subscription_id: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> DBTeamSpendPeriod:
    budget_type = _resolve_budget_type(team)
    row = _query_spend_period(
        db, team.id, region_id, budget_type, period_start, period_end
    )
    if row is None:
        new_row = DBTeamSpendPeriod(
            team_id=team.id,
            region_id=region_id,
            budget_type=budget_type,
            period_start=period_start,
            period_end=period_end,
            source=source,
            created_at=datetime.now(UTC),
        )
        try:
            # Use a savepoint so that a concurrent insert only rolls back the
            # nested transaction, leaving the outer transaction intact.
            with db.begin_nested():
                db.add(new_row)
                db.flush()
            row = new_row
        except IntegrityError:
            # Another concurrent task inserted the same row; the savepoint was
            # rolled back, so the outer transaction is still valid – re-fetch.
            row = _query_spend_period(
                db, team.id, region_id, budget_type, period_start, period_end
            )
            if row is None:
                raise RuntimeError(
                    f"Concurrent insert race: spend period not found after IntegrityError "
                    f"(team_id={team.id} region_id={region_id} "
                    f"window={period_start} to {period_end})"
                )

    row.currency = None
    row.total_spend = float(snapshot.get("total_spend", 0.0) or 0.0)
    row.total_budget = (
        float(snapshot["total_budget"])
        if snapshot.get("total_budget") is not None
        else None
    )
    row.total_prompt_tokens = snapshot.get("total_prompt_tokens")
    row.total_completion_tokens = snapshot.get("total_completion_tokens")
    row.total_tokens = snapshot.get("total_tokens")
    row.source = source
    row.stripe_event_id = stripe_event_id
    row.stripe_invoice_id = stripe_invoice_id
    row.stripe_subscription_id = stripe_subscription_id
    row.raw_payload = raw_payload
    db.add(row)
    db.flush()

    db.query(DBTeamSpendPeriodKey).filter(
        DBTeamSpendPeriodKey.team_spend_period_id == row.id
    ).delete()

    for item in snapshot.get("keys", []):
        db.add(
            DBTeamSpendPeriodKey(
                team_spend_period_id=row.id,
                key_id=item.get("key_id"),
                owner_id=item.get("owner_id"),
                key_name_snapshot=item.get("key_name_snapshot"),
                spend=float(item.get("spend", 0.0) or 0.0),
                max_budget=(
                    float(item["max_budget"])
                    if item.get("max_budget") is not None
                    else None
                ),
                prompt_tokens=item.get("prompt_tokens"),
                completion_tokens=item.get("completion_tokens"),
                total_tokens=item.get("total_tokens"),
            )
        )

    db.flush()
    return row
