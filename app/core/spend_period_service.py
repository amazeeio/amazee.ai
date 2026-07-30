from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    DBPeriodicBudgetLedgerEntry,
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


def current_cycle_start(
    budget_duration: str | None, anchor: datetime | None, now: datetime
) -> datetime | None:
    """Start of the cycle *containing now* for a per-cycle budget.

    Spend caps are per cycle, not absolute: on PROD every one of them is ``31d``
    or ``1mo``, and LiteLLM zeroes the key's spend at each boundary. A percentage
    against such a cap therefore has to be summed over that cap's current cycle —
    dividing a longer stretch of spend by a one-month cap reads far above 100 %
    and fires alerts nobody has earned.

    This differs from :func:`compute_period_start` in rolling forward. That one
    derives the window from LiteLLM's ``budget_reset_at``, which can sit in the
    past (PROD has keys whose ``budget_reset_at`` is a month behind), and would
    then hand back a window that ended long ago. Here the anchor is stepped by
    whole cycles until it contains ``now``, which is what LiteLLM does when it
    actually resets.

    ``1mo``/``30d`` snap to the calendar month, matching LiteLLM's own rule and
    the ``budget_reset_at`` values it stores (always the 1st).
    """
    if not budget_duration:
        return None

    if budget_duration in ("1mo", "30d"):
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    match = re.fullmatch(r"(\d+)d", str(budget_duration).strip())
    if not match:
        return None
    days = int(match.group(1))
    if days <= 0:
        return None

    start = _as_utc(anchor) or now
    if start >= now:
        return start
    whole_cycles = (now - start).days // days
    return start + timedelta(days=whole_cycles * days)


def compute_period_start(
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(frozen=True)
class TeamPeriodWindow:
    """The current budget window for one (team, region) pair.

    ``period_end`` is the same instant LiteLLM calls ``budget_reset_at``.
    ``source`` records which rule produced the window, which makes a wrong
    percentage traceable to a rule rather than to arithmetic.
    """

    period_start: datetime | None
    period_end: datetime | None
    budget_duration: str | None
    source: str
    active_subscription: DBPeriodicBudgetLedgerEntry | None = None

    @property
    def period_key(self) -> str:
        """Stable identity for this window, used to re-arm threshold alerts.

        A new billing cycle yields a different key, which is what makes an
        already-notified threshold fire again in the next period.
        """
        if self.period_start is None:
            return f"{self.source}:none"
        return f"{self.source}:{self.period_start.isoformat()}"


def _active_subscription_entry(
    db: Session, team_id: int, region_id: int, now: datetime
) -> DBPeriodicBudgetLedgerEntry | None:
    return (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "subscription",
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.effective_period_start.isnot(None),
            DBPeriodicBudgetLedgerEntry.effective_period_end.isnot(None),
            DBPeriodicBudgetLedgerEntry.effective_period_end > now,
        )
        .order_by(
            DBPeriodicBudgetLedgerEntry.effective_period_end.desc(),
            DBPeriodicBudgetLedgerEntry.id.desc(),
        )
        .first()
    )


def _latest_active_topup(
    db: Session, team_id: int, region_id: int, now: datetime
) -> DBPeriodicBudgetLedgerEntry | None:
    return (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team_id,
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.entry_type.in_(["topup", "topup_rollover"]),
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
        )
        .order_by(
            DBPeriodicBudgetLedgerEntry.purchased_at.desc(),
            DBPeriodicBudgetLedgerEntry.id.desc(),
        )
        .first()
    )


def resolve_team_period_window(
    db: Session,
    team: DBTeam,
    region_id: int,
    *,
    litellm_budget_duration: str | None = None,
    litellm_budget_reset_at: datetime | None = None,
    now: datetime | None = None,
) -> TeamPeriodWindow:
    """Resolve the active budget window for a (team, region) pair.

    Single source of truth for "which period are we in", shared by the spend
    API and the budget-threshold alert engine. If these two ever disagree, a
    customer sees one percentage in the dashboard and gets alerted on another.

    POOL teams are resolved purely from our own ledger — LiteLLM's counters are
    not authoritative for them (the budget is pushed there, not owned there).
    PERIODIC teams prefer the LiteLLM window when the caller has already
    fetched team info, and fall back to the ledger otherwise, so a caller that
    cannot afford a LiteLLM round-trip still gets a usable window.
    """
    now = now or datetime.now(UTC)

    if team.budget_type == BudgetType.POOL:
        active_subscription = _active_subscription_entry(db, team.id, region_id, now)
        if active_subscription is not None:
            return TeamPeriodWindow(
                period_start=_as_utc(active_subscription.effective_period_start),
                period_end=_as_utc(active_subscription.effective_period_end),
                # Stripe cycles are 30d; LiteLLM carries 31d as the missed-webhook
                # safety net, matching apply_billing_cycle_for_team.
                budget_duration="31d",
                source="subscription_ledger",
                active_subscription=active_subscription,
            )

        # No subscription: the window is the top-up purchase lifetime.
        active_topup = _latest_active_topup(db, team.id, region_id, now)
        anchor = _as_utc(
            (active_topup.purchased_at if active_topup is not None else None)
            or team.created_at
            or now
        )
        return TeamPeriodWindow(
            period_start=anchor,
            period_end=anchor + timedelta(days=settings.POOL_PURCHASE_EXPIRY_DAYS),
            budget_duration=f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d",
            source="pool_topup",
        )

    # --- PERIODIC ---
    if litellm_budget_reset_at is not None and litellm_budget_duration:
        derived_start = compute_period_start(
            litellm_budget_reset_at, litellm_budget_duration
        )
        if derived_start is not None:
            return TeamPeriodWindow(
                period_start=_as_utc(derived_start),
                period_end=_as_utc(litellm_budget_reset_at),
                budget_duration=litellm_budget_duration,
                source="litellm",
            )

    active_subscription = _active_subscription_entry(db, team.id, region_id, now)
    if active_subscription is not None:
        return TeamPeriodWindow(
            period_start=_as_utc(active_subscription.effective_period_start),
            period_end=_as_utc(active_subscription.effective_period_end),
            budget_duration="31d",
            source="subscription_ledger",
            active_subscription=active_subscription,
        )

    anchor = _as_utc(team.last_payment or team.created_at or now)
    return TeamPeriodWindow(
        period_start=anchor,
        period_end=anchor + timedelta(days=31),
        budget_duration="31d",
        source="team_anchor",
    )


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
    subscription_remaining_cents: int | None = None,
    topup_remaining_cents: int | None = None,
    desired_remaining_cents: int | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> DBTeamSpendPeriod:
    """Insert a spend-period snapshot for a specific team/region/window.

    This function is intentionally "first write wins": if a spend period row already exists
    for the same window, the existing row is returned unchanged (no updates to totals, keys,
    or metadata).
    """
    budget_type = _resolve_budget_type(team)
    row = _query_spend_period(
        db, team.id, region_id, budget_type, period_start, period_end
    )
    is_new_row = False
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
            is_new_row = True
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

    if not is_new_row:
        return row

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
    row.subscription_remaining_cents = subscription_remaining_cents
    row.topup_remaining_cents = topup_remaining_cents
    row.desired_remaining_cents = desired_remaining_cents
    row.source = source
    row.stripe_event_id = stripe_event_id
    row.stripe_invoice_id = stripe_invoice_id
    row.stripe_subscription_id = stripe_subscription_id
    row.raw_payload = raw_payload
    db.add(row)
    db.flush()

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
