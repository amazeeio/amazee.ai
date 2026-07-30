"""Budget threshold alerts — detect when spend crosses a share of budget.

Emits an event when a team, key or team member crosses 50 / 75 / 90 / 100 % of
its budget, so a downstream consumer (MOAD) can warn the customer *before* their
keys stop working.

Scope: **POOL teams**, and keys carrying both a team and a region — the shape MOAD
provisions and can actually notify. PERIODIC is excluded because on PROD every
PERIODIC key (12,098 of them) belongs to the single anonymous Drupal trial team,
which has no MOAD workspace and no addressable owner. Note that the *period-window
helper* still handles PERIODIC, since the spend API shares it.

Why amazee.ai computes the percentage instead of LiteLLM
-------------------------------------------------------
LiteLLM has native budget alerting, but it cannot produce the right number for
our teams:

* **Wrong numerator for PERIODIC teams.** LiteLLM's team-level ``spend`` counter
  is never reset; our billing cycle zeroes the *per-key* spends instead. Dividing
  a lifetime total by a one-cycle budget sits permanently above 100 %. The spend
  API works around this by summing key spends (``app/api/spend.py``), and so do we.
* **Moving denominator for POOL teams.** The budget is owned by our ledger and
  pushed to LiteLLM. Buying a top-up raises it, so a percentage can fall back
  below a band it already crossed. LiteLLM's TTL-based dedup would suppress the
  genuine second crossing.
* Its thresholds are hardcoded to 85 % / 95 % and are not configurable.

So LiteLLM is the source of *spend*, and every budget number comes from our DB.

How it scales to 100k keys
--------------------------
Two stages, both bounded by *activity* rather than by key count:

1. **Discovery and spend** — an unfiltered ``/team/daily/activity`` sweep over the
   window that ``region_sweep_start`` derives from the ledger. LiteLLM writes no
   daily rows for idle entities, so on PROD region 5 an 11,859-key region reported
   42 active teams and 351 active keys. Measured cost is 3 pages / ~1 s at the full
   370-day span (page 1 alone at 62 days was 1.08 s), and usually less, because the
   window only reaches back as far as the oldest unexpired purchase.
   ``_fetch_daily_activity`` follows ``has_more``, and pages partition the data
   rather than repeating it, so the per-day totals add up cleanly.
2. **Denominators for keys** — one scoped ``/key/list?team_id=`` per candidate team,
   used for ``max_budget`` and to spot keys being expired. Its ``spend`` figures are
   deliberately ignored; spend comes from the daily rows instead.

So a tick costs ``pages + active_teams`` calls per region, independent of how many
idle keys exist, plus one scoped back-fill for any team whose budget window opens
before the wide request (none on PROD today). Sweeping every key instead — the
shape of the existing hourly reconciler, ~40 min at 13.9k keys — is what does not
survive 100k.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from prometheus_client import Counter, Gauge, Summary
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.pool_budget_service import pool_available_budget_for_team_region
from app.core.spend_period_service import TeamPeriodWindow, resolve_team_period_window
from app.db.models import (
    DBAuditLog,
    DBBudgetAlertState,
    DBLimitedResource,
    DBPeriodicBudgetLedgerEntry,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.schemas.limits import OwnerType, ResourceType
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

SUBJECT_TEAM = "team"
SUBJECT_KEY = "key"
SUBJECT_TEAM_MEMBER = "team_member"

budget_alerts_fired_total = Counter(
    "budget_alerts_fired_total",
    "Budget threshold crossings detected",
    ["scope", "threshold"],
)

budget_alert_delivery_failures_total = Counter(
    "budget_alert_delivery_failures_total",
    "Budget threshold events that were detected but not delivered",
)

budget_alert_subjects_skipped_total = Counter(
    "budget_alert_subjects_skipped_total",
    "Subjects that could not be evaluated safely and were dropped",
    ["reason"],
)

budget_alert_subjects_evaluated = Gauge(
    "budget_alert_subjects_evaluated",
    "Subjects evaluated in the last budget threshold sweep, per region",
    ["region_name"],
)

budget_alert_run_duration = Summary(
    "budget_alert_run_duration_seconds",
    "Time taken to complete the budget threshold sweep",
)

# LiteLLM's own bookkeeping teams show up in the activity breakdown; they are not
# amazee.ai teams and have no budget of ours.
_NON_TEAM_ENTITY_IDS = frozenset({"litellm-dashboard"})


def parse_thresholds(raw: str | None = None) -> list[int]:
    """Parse the configured thresholds into a sorted, de-duplicated list."""
    raw = raw if raw is not None else settings.BUDGET_ALERT_THRESHOLDS
    values: set[int] = set()
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(float(chunk))
        except (TypeError, ValueError):
            logger.warning("Ignoring unparseable budget alert threshold: %r", chunk)
            continue
        if 0 < value <= 1000:
            values.add(value)
    return sorted(values)


def highest_crossed_band(percent_used: float, thresholds: list[int]) -> int:
    """Return the highest threshold at or below ``percent_used`` (0 if none).

    Only the highest band is ever reported, so a key that jumps from 0 % to 95 %
    in one tick produces a single 90 % event rather than 50 + 75 + 90.
    """
    crossed = 0
    for threshold in thresholds:
        if percent_used >= threshold:
            crossed = threshold
    return crossed


@dataclass(frozen=True)
class BudgetAlertEvent:
    """One threshold crossing, ready to serialise for the webhook."""

    event_id: str
    subject_type: str
    subject_key: str
    threshold_pct: int
    percent_used: float
    spend: float
    max_budget: float
    region_id: int
    region_name: str
    period_key: str
    period_start: datetime | None
    period_end: datetime | None
    budget_duration: str | None
    budget_source: str = "team_ledger"
    team_id: int | None = None
    team_name: str | None = None
    user_id: int | None = None
    user_email: str | None = None
    key_id: int | None = None
    key_name: str | None = None
    # Service keys belong to the team itself; user keys have an owner. Both are in
    # scope, and consumers route the notification differently for each.
    is_service_key: bool = False


@dataclass
class _Subject:
    """A thing with a budget, evaluated against the thresholds."""

    subject_type: str
    subject_key: str
    spend: float
    max_budget: float | None
    window: TeamPeriodWindow
    team: DBTeam | None = None
    user: DBUser | None = None
    key: DBPrivateAIKey | None = None
    # Where the denominator came from. Consumers need this to word the message:
    # "90% of this key's limit" and "90% of the team pool" are different sentences.
    budget_source: str = "team_ledger"

    @property
    def percent_used(self) -> float:
        if not self.max_budget or self.max_budget <= 0:
            return 0.0
        return round((self.spend / self.max_budget) * 100.0, 4)


@dataclass
class RegionEvaluation:
    """Result of evaluating one region."""

    events: list[BudgetAlertEvent] = field(default_factory=list)
    # Subjects whose band dropped or whose period rolled over. Their state is
    # rewritten without notifying, so a later genuine re-crossing still fires.
    resets: list[tuple[_Subject, int]] = field(default_factory=list)
    # Every subject considered this tick, kept so a delivered event can be
    # re-joined to the ORM objects behind it. Events stay plain data because they
    # get serialised; subjects hold ORM instances and never leave this module.
    subjects: list[_Subject] = field(default_factory=list)
    litellm_calls: int = 0

    @property
    def subjects_evaluated(self) -> int:
        return len(self.subjects)


# --------------------------------------------------------------------------- #
# Spend collection
# --------------------------------------------------------------------------- #


def _active_entity_ids(rows: list[dict]) -> set[str]:
    """Entity ids that appear anywhere in a daily-activity sweep (discovery)."""
    entity_ids: set[str] = set()
    for row in rows:
        breakdown = row.get("breakdown") or {}
        entity_ids.update((breakdown.get("entities") or {}).keys())
    return entity_ids


def _sum_activity_since(
    rows: list[dict], since: date
) -> tuple[dict[str, float], dict[str, float]]:
    """Sum per-day activity from ``since`` onwards, by entity and by hashed key.

    Returns ``(entity_spend, key_spend)``. Rows are one per UTC day, so a window
    opening mid-day includes that whole day — an overstatement bounded by one day.
    """
    entity_spend: dict[str, float] = {}
    key_spend: dict[str, float] = {}

    for row in rows:
        raw_date = row.get("date")
        try:
            row_date = date.fromisoformat(str(raw_date)[:10])
        except (TypeError, ValueError):
            logger.warning("Skipping daily-activity row with bad date: %r", raw_date)
            continue
        if row_date < since:
            continue

        breakdown = row.get("breakdown") or {}
        for entity_id, payload in (breakdown.get("entities") or {}).items():
            metrics = (payload or {}).get("metrics") or {}
            entity_spend[entity_id] = entity_spend.get(entity_id, 0.0) + float(
                metrics.get("spend") or 0.0
            )
        for hashed_token, payload in (breakdown.get("api_keys") or {}).items():
            metrics = (payload or {}).get("metrics") or {}
            key_spend[hashed_token] = key_spend.get(hashed_token, 0.0) + float(
                metrics.get("spend") or 0.0
            )

    return entity_spend, key_spend


def spend_window_start(window: TeamPeriodWindow, now: datetime) -> datetime:
    """When the spend that counts against the current budget began.

    Always the start of the **current cycle**, and deliberately nothing else:

    * subscription team -> the cycle start (``effective_period_start``);
    * top-up-only team  -> the last top-up purchase;
    * neither           -> team creation.

    Those are exactly the windows ``resolve_team_period_window`` already resolves
    for the spend API, so this is a thin read of it rather than a second rule. An
    alert has to quote the number the dashboard shows, and two independent window
    calculations would eventually disagree.

    Reaching back further than the cycle start would *double count*: the
    denominator is ``purchased - consumed_cents``, and ``consumed_cents`` is
    incremented at cycle close while LiteLLM's spend is reset on the same call
    (see the invariant at ``app/api/spend.py``). A closed cycle's spend is
    therefore already subtracted from the budget, so counting it again in the
    numerator inflates the percentage and fires alerts nobody has earned.
    """
    return window.period_start or now


def _amazee_team_id_from_litellm(entity_id: str, region_name: str) -> int | None:
    """Recover our team id from LiteLLM's ``<region>_<team_id>`` team id."""
    if entity_id in _NON_TEAM_ENTITY_IDS:
        return None
    prefix = f"{region_name.replace(' ', '_')}_"
    if not entity_id.startswith(prefix):
        return None
    suffix = entity_id[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


async def _exact_team_key_state(
    service: LiteLLMService, lite_team_id: str
) -> dict[str, dict]:
    """Current per-key ``spend``/``max_budget`` for one team, keyed by hashed token.

    Supplies the ``max_budget`` a key is measured against when our ``spend_caps``
    hold none, and flags keys being expired via ``budget_duration``. Its ``spend``
    is never read: LiteLLM's key counters are lifetime totals that a top-up does
    not reset, so they cannot be divided by a budget that excludes expired
    entries. Spend always comes from the daily-activity rows.
    """
    try:
        keys = await service.list_keys_for_team(lite_team_id)
    except Exception as exc:
        logger.warning("Could not fetch exact key state for %s: %s", lite_team_id, exc)
        return {}
    return {
        str(key.get("token")): key
        for key in keys
        if isinstance(key, dict) and key.get("token")
    }


# --------------------------------------------------------------------------- #
# Denominators — always ours, never LiteLLM's
# --------------------------------------------------------------------------- #


def _team_budget(db: Session, team: DBTeam, region_id: int) -> float | None:
    """Budget available to a team in a region, from our ledger.

    Mirrors what the spend API reports as ``period_budget``: remaining
    subscription entries plus remaining top-ups. An operator cap
    (``spend_caps`` scope ``team``) only ever tightens it.
    """
    available = pool_available_budget_for_team_region(db, team.id, region_id)
    if available <= 0 and team.budget_type != BudgetType.POOL:
        # A PERIODIC team with no ledger yet (e.g. a trial) still carries a BUDGET
        # limit, which is the only budget it has.
        #
        # This deliberately does *not* apply to POOL teams. A POOL team's budget is
        # the money it bought, so no valid ledger entry means nothing to be a
        # percentage of, and the BUDGET limit is a provisioning allowance rather
        # than available credit. On PROD 673 POOL teams sit in exactly that state
        # (662 never purchased at all) carrying a mostly-$27 limit; measuring
        # spend against it would invent a percentage, and with no purchase to anchor
        # on there is no cycle start either, so the window would fall back to team
        # creation. Such a team simply gets no team event; its keys are still
        # evaluated if they carry their own cap.
        limit_value = (
            db.query(DBLimitedResource.max_value)
            .filter(
                DBLimitedResource.owner_type == OwnerType.TEAM,
                DBLimitedResource.owner_id == team.id,
                DBLimitedResource.resource == ResourceType.BUDGET,
            )
            .scalar()
        )
        available = float(limit_value) if limit_value else 0.0

    cap = (
        db.query(DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team.id,
            DBSpendCap.max_budget.isnot(None),
        )
        .scalar()
    )
    if cap is not None:
        available = min(available, float(cap))
    return available if available > 0 else None


def _member_budget(
    db: Session, team_id: int, user_id: int, region_id: int
) -> float | None:
    """Per-member budget: the team-member cap, else the user's BUDGET limit."""
    cap = (
        db.query(DBSpendCap.max_budget)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id == team_id,
            DBSpendCap.user_id == user_id,
            DBSpendCap.max_budget.isnot(None),
        )
        .scalar()
    )
    if cap is not None:
        return float(cap) if float(cap) > 0 else None

    limit_value = (
        db.query(DBLimitedResource.max_value)
        .filter(
            DBLimitedResource.owner_type == OwnerType.USER,
            DBLimitedResource.owner_id == user_id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .scalar()
    )
    if limit_value and float(limit_value) > 0:
        return float(limit_value)
    return None


def _key_cap_map(db: Session, region_id: int, key_ids: list[int]) -> dict[int, float]:
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
    return {int(key_id): float(value) for key_id, value in rows if value}


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _sweep_start(now: datetime) -> date:
    """The hard floor on how far back a sweep will ever ask LiteLLM to go."""
    return (now - timedelta(days=settings.BUDGET_ALERT_MAX_LOOKBACK_DAYS)).date()


def region_sweep_start(db: Session, region_id: int, now: datetime) -> date:
    """First day the activity sweep must cover for this region.

    Each team's spend is summed from its own window start (see
    ``spend_window_start``), so a sweep that begins *after* that date returns only
    part of the team's spend. Summing a partial answer does not merely lose
    precision — the percentage stays permanently understated, so a team truly at
    90 % is reported at 55 % and gets told it is fine. The fix is to make the
    request cover the earliest window in the region rather than a fixed span.

    It anchors on the oldest still-valid purchase, which is a *conservative* lower
    bound: a cycle start is always at or after the purchase that funded it, so this
    can fetch a few more days than any team needs but can never fetch too few.

    Anchoring on the ledger also usually makes the sweep *cheaper* than the
    370-day default: most POOL money was bought recently, so the request narrows
    to the days that can matter. ``BUDGET_ALERT_MAX_LOOKBACK_DAYS`` remains a hard
    floor on this one wide request; a team whose window opens before it is
    back-filled with its own scoped call in ``evaluate_region`` rather than being
    summed from partial rows.
    """
    floor = _sweep_start(now)
    oldest_anchor = (
        db.query(func.min(DBPeriodicBudgetLedgerEntry.purchased_at))
        .join(DBTeam, DBTeam.id == DBPeriodicBudgetLedgerEntry.team_id)
        .filter(
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.is_active.is_(True),
            DBPeriodicBudgetLedgerEntry.purchased_at.isnot(None),
            (
                DBPeriodicBudgetLedgerEntry.expires_at.is_(None)
                | (DBPeriodicBudgetLedgerEntry.expires_at > now)
            ),
            DBTeam.deleted_at.is_(None),
            DBTeam.budget_type == BudgetType.POOL,
        )
        .scalar()
    )
    if oldest_anchor is None:
        return floor
    return max(floor, oldest_anchor.date())


def _is_expiring(key_state: dict) -> bool:
    """A key set to ``0d`` is being expired; it is not a budget problem."""
    return str(key_state.get("budget_duration") or "") == "0d"


def denominator_change_candidates(
    db: Session, region_id: int, now: datetime
) -> set[int]:
    """Teams whose *budget* just fell, regardless of whether they spent anything.

    Traffic-driven discovery rests on "percent only rises when spend rises", which
    is false: the denominator moves too. On PROD every active ledger entry carries
    an ``expires_at`` (321 of them due to expire), so a lapsing top-up or
    subscription drops a team's remaining budget and can push it past 90 % while
    it sits completely idle. Such a team produces no daily-activity rows, so
    without this it would never be looked at and the warning would never fire —
    the exact scenario the feature exists to prevent.

    Two sources, both cheap because they are bounded by *changes* rather than by
    team count:

    * a ledger entry that expired within the recheck grace window;
    * a spend cap edited within the same window (lowering a cap shrinks the
      denominator just as effectively as an expiry).

    The grace window spans several ticks on purpose. Re-notification is not a risk
    — ``budget_alert_state`` suppresses a band already sent — so it is better to
    look a few times too often than to miss the one tick the change landed in.
    """
    grace_hours = max(1, settings.BUDGET_ALERT_RECHECK_GRACE_HOURS)
    since = now - timedelta(hours=grace_hours)

    expired_team_ids = {
        team_id
        for (team_id,) in db.query(DBPeriodicBudgetLedgerEntry.team_id)
        .filter(
            DBPeriodicBudgetLedgerEntry.region_id == region_id,
            DBPeriodicBudgetLedgerEntry.expires_at.isnot(None),
            DBPeriodicBudgetLedgerEntry.expires_at <= now,
            DBPeriodicBudgetLedgerEntry.expires_at >= since,
        )
        .distinct()
        .all()
        if team_id is not None
    }

    capped_team_ids = {
        team_id
        for (team_id,) in db.query(DBSpendCap.team_id)
        .filter(
            DBSpendCap.region_id == region_id,
            DBSpendCap.team_id.isnot(None),
            DBSpendCap.updated_at.isnot(None),
            DBSpendCap.updated_at >= since,
        )
        .distinct()
        .all()
        if team_id is not None
    }

    candidates = expired_team_ids | capped_team_ids
    if candidates:
        logger.info(
            "Region %s: %d team(s) rechecked for a budget decrease "
            "(%d expiry, %d cap change)",
            region_id,
            len(candidates),
            len(expired_team_ids),
            len(capped_team_ids),
        )
    return candidates


async def evaluate_region(
    db: Session,
    region: DBRegion,
    *,
    thresholds: list[int] | None = None,
    now: datetime | None = None,
) -> RegionEvaluation:
    """Evaluate every active subject in one region against the thresholds."""
    thresholds = thresholds or parse_thresholds()
    now = now or datetime.now(UTC)
    result = RegionEvaluation()
    if not thresholds:
        return result

    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    sweep_start = region_sweep_start(db, region.id, now)
    start_str = sweep_start.isoformat()
    end_str = now.date().isoformat()

    try:
        team_rows = await service.get_all_team_daily_activity(start_str, end_str)
        result.litellm_calls += 1
    except Exception as exc:
        logger.error("Budget alert sweep failed for region %s: %s", region.name, exc)
        return result

    subjects: list[_Subject] = []

    # --- candidate teams --------------------------------------------------- #
    # Only entities that actually spent appear in the sweep, so this is the
    # working set: on a DEV region with 2,740 keys it was 27 teams. A team with
    # no traffic in the lookback cannot have newly crossed a band, since spend
    # only rises with traffic and we notify on upward moves only.
    candidate_team_ids = {
        team_id
        for entity_id in _active_entity_ids(team_rows)
        if (team_id := _amazee_team_id_from_litellm(entity_id, region.name)) is not None
    }

    # Traffic is not the only way to cross a threshold: percent = spend / budget,
    # and the *budget* can fall on its own. These teams therefore have to be
    # evaluated even though they were silent.
    denominator_change_team_ids = denominator_change_candidates(db, region.id, now)
    candidate_team_ids.update(denominator_change_team_ids)

    if not candidate_team_ids:
        logger.info("No active teams in region %s this sweep", region.name)
        return result

    # POOL only. These are the teams MOAD provisions and can actually notify —
    # every PERIODIC key on PROD (12,098 of them) belongs to the single anonymous
    # Drupal trial team, which has no MOAD workspace and no addressable owner, so
    # a threshold webhook for it would have nowhere to go.
    teams = (
        db.query(DBTeam)
        .filter(
            DBTeam.id.in_(candidate_team_ids),
            DBTeam.deleted_at.is_(None),
            DBTeam.budget_type == BudgetType.POOL,
        )
        .all()
    )
    for team in teams:
        window = resolve_team_period_window(db, team, region.id, now=now)
        lite_team_id = LiteLLMService.format_team_id(region.name, team.id)

        # Alerts are per cycle, so spend is summed from the current cycle's start:
        # the subscription's effective_period_start, or the last top-up purchase for
        # a team that only buys top-ups. Never from LiteLLM's key counters, which
        # are lifetime totals a top-up does not reset.
        since_dt = spend_window_start(window, now)
        since = since_dt.date()

        # Only keys carrying both a team and a region — the shape MOAD provisions.
        # A key with just an owner and no team has no team budget to be a share of,
        # and no workspace to notify, so it is out of scope by design rather than
        # by omission. On PROD that is 418 of 13,930 keys.
        db_keys = (
            db.query(DBPrivateAIKey)
            .filter(
                DBPrivateAIKey.team_id == team.id,
                DBPrivateAIKey.region_id == region.id,
                DBPrivateAIKey.litellm_token.isnot(None),
            )
            .all()
        )
        cap_map = _key_cap_map(db, region.id, [key.id for key in db_keys])
        team_budget = _team_budget(db, team, region.id)

        if since >= sweep_start:
            entity_totals, key_totals = _sum_activity_since(team_rows, since)
        elif not (team_budget or cap_map):
            # Nothing to measure, so nothing to fetch. A POOL team with no valid
            # ledger entry anchors on team.created_at, which is routinely older than
            # the window, and there are 673 of those on PROD — spending a call on
            # each would cost a call per team per tick for no possible event.
            continue
        else:
            # The wide sweep starts after this team's window opens, so its rows hold
            # only part of the team's spend. Summing them anyway would understate the
            # percentage for good — not merely imprecisely — because the missing days
            # never enter the window: a team truly at 92 % would be told it is at
            # 30 %, and would never reach the 90 band at all.
            #
            # So the team is back-filled with a scoped call over its own window
            # instead. /team/daily/activity takes a team_ids filter, and one team's
            # rows are bounded by the number of days, so this stays cheap. It costs
            # one extra call only for teams the wide sweep cannot cover — none on
            # PROD today, where no anchor is older than 69 days against a 370-day
            # floor. Skipping such a team instead would hide every crossing it has
            # until the floor was raised by hand.
            logger.info(
                "Team %s window opens %s, before the %s sweep start; "
                "back-filling its spend with a scoped activity call",
                team.id,
                since,
                sweep_start,
            )
            try:
                scoped_rows = await service.get_team_daily_activity(
                    lite_team_id, since.isoformat(), end_str
                )
                result.litellm_calls += 1
            except Exception as exc:
                # Without the back-fill the only honest options are a wrong number
                # or no number; take no number, and make the reason visible.
                logger.error(
                    "Team %s skipped: could not back-fill spend from %s: %s",
                    team.id,
                    since,
                    exc,
                )
                budget_alert_subjects_skipped_total.labels(
                    reason="activity_backfill_failed"
                ).inc()
                continue
            entity_totals, key_totals = _sum_activity_since(scoped_rows, since)

        # /key/list supplies the key *denominators* (max_budget) and flags keys being
        # expired; its spend figures are deliberately not used.
        exact_keys = await _exact_team_key_state(service, lite_team_id)
        result.litellm_calls += 1

        # An uncapped key's only budget is the team pool, so that is what it is
        # measured against — it answers "which key is eating the pool", which the
        # team event alone cannot. Withheld when the team has a single in-scope key,
        # because then the key percentage is arithmetically identical to the team's
        # and the alert would just be the team event repeated. That is the common
        # shape: 481 of 739 POOL teams on PROD hold exactly one key.
        pool_fallback_budget = team_budget if len(db_keys) > 1 else None

        member_spend: dict[int, float] = {}
        # The team total is the sum of its keys' windowed spend, so team and key
        # percentages are always drawn from the same numbers.
        team_spend = 0.0

        for db_key in db_keys:
            hashed = LiteLLMService.hash_token(db_key.litellm_token)
            key_state = exact_keys.get(hashed)
            key_spend = float(key_totals.get(hashed, 0.0))
            litellm_max_budget = (
                key_state.get("max_budget") if key_state is not None else None
            )

            # Money a key spent counts towards its team and its owner whatever the
            # key's own state, so these totals are accumulated before any of the
            # per-key skips below. Dropping an expiring key's spend here would
            # understate the team percentage and make the reconciliation warning
            # further down fire on a gap that does not exist.
            team_spend += key_spend
            if db_key.owner_id:
                member_spend[db_key.owner_id] = (
                    member_spend.get(db_key.owner_id, 0.0) + key_spend
                )

            if key_state is not None and _is_expiring(key_state):
                # Being expired via budget_duration "0d". No key-scope alert: the
                # key is on its way out, which is not a budget problem.
                continue

            # Denominator precedence: our own cap, then whatever LiteLLM enforces,
            # then the team pool. Applies equally to service keys (no owner) and
            # user keys — a key is in scope for having a team, not for having an
            # owner. On PROD: 405 service and 929 user keys across POOL teams.
            key_budget = cap_map.get(db_key.id)
            budget_source = "key_cap"
            if key_budget is None and litellm_max_budget is not None:
                key_budget = float(litellm_max_budget)
                budget_source = "litellm_key_budget"
            if not key_budget or key_budget <= 0:
                # A budget of <= 0 is how a pool-gated key is born blocked;
                # reporting it as "100% used" at creation would be pure noise.
                # An absent budget instead means the key is bounded by the pool.
                if litellm_max_budget is not None and float(litellm_max_budget) <= 0:
                    continue
                key_budget = pool_fallback_budget
                budget_source = "team_pool"
            if not key_budget or key_budget <= 0:
                continue

            subjects.append(
                _Subject(
                    subject_type=SUBJECT_KEY,
                    subject_key=f"key:{db_key.id}",
                    spend=key_spend,
                    max_budget=key_budget,
                    window=window,
                    team=team,
                    key=db_key,
                    budget_source=budget_source,
                )
            )

        # The entity total covers every key LiteLLM attributes to the team, including
        # any we do not have a row for. A gap means our key table is out of sync, and
        # the team percentage would be understated, so it is worth saying so.
        entity_total = float(entity_totals.get(lite_team_id, 0.0))
        if entity_total - team_spend > 0.01:
            logger.warning(
                "Team %s region %s: LiteLLM attributes %.4f but our keys account for "
                "%.4f; %.4f of spend belongs to keys missing from ai_tokens",
                team.id,
                region.id,
                entity_total,
                team_spend,
                entity_total - team_spend,
            )

        subjects.append(
            _Subject(
                subject_type=SUBJECT_TEAM,
                subject_key=f"team:{team.id}:{region.id}",
                spend=team_spend,
                max_budget=team_budget,
                window=window,
                team=team,
            )
        )

        # --- team members -------------------------------------------------- #
        if member_spend:
            users = (
                db.query(DBUser).filter(DBUser.id.in_(list(member_spend.keys()))).all()
            )
            for user in users:
                subjects.append(
                    _Subject(
                        subject_type=SUBJECT_TEAM_MEMBER,
                        subject_key=f"member:{team.id}:{user.id}:{region.id}",
                        spend=member_spend.get(user.id, 0.0),
                        max_budget=_member_budget(db, team.id, user.id, region.id),
                        window=window,
                        team=team,
                        user=user,
                    )
                )

    result.subjects = subjects
    _classify_subjects(db, region, subjects, thresholds, result)
    return result


def _classify_subjects(
    db: Session,
    region: DBRegion,
    subjects: list[_Subject],
    thresholds: list[int],
    result: RegionEvaluation,
) -> None:
    """Split subjects into new crossings to notify and states to quietly reset."""
    if not subjects:
        return

    existing = {
        row.subject_key: row
        for row in db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key.in_([s.subject_key for s in subjects]))
        .all()
    }

    for subject in subjects:
        if not subject.max_budget or subject.max_budget <= 0:
            continue
        band = highest_crossed_band(subject.percent_used, thresholds)
        state = existing.get(subject.subject_key)
        period_key = subject.window.period_key

        previous_band = 0
        if state is not None and state.period_key == period_key:
            previous_band = int(state.last_threshold_pct or 0)

        # The arming this crossing belongs to. Every silent reset bumps it, which is
        # what lets a genuine re-crossing of an already-notified band carry a new
        # event_id instead of colliding with the first one. See event_id_for.
        arm_seq = int(state.arm_seq or 0) if state is not None else 0

        if band > previous_band:
            result.events.append(
                _build_event(region, subject, band, period_key, arm_seq)
            )
        elif band != previous_band or (
            state is not None and state.period_key != period_key
        ):
            # Either the period rolled over, or the percentage fell — a POOL
            # top-up raises the denominator. Rewrite the band silently so a real
            # re-crossing later still notifies, but never announce a decrease.
            result.resets.append((subject, band))


def event_id_for(subject_key: str, period_key: str, band: int, arm_seq: int) -> str:
    """A stable id for one crossing, so a retry carries the id of the original.

    Delivery is at-least-once: a POST whose response is lost leaves the state
    unadvanced, and the next tick rebuilds the same crossing. A random id would
    make that retry look like a second, separate crossing to the consumer, and its
    de-duplication on ``event_id`` — which is the only thing protecting the
    customer from a duplicate notification — could not catch it.

    ``arm_seq`` is what keeps that from going too far. A band can legitimately
    recur inside one period: a POOL top-up raises the denominator, the percentage
    falls, the band is silently re-armed, and later spend crosses the same band
    again. That second crossing is a real event the customer must hear about. It is
    only distinguishable from a retry because ``arm_seq`` was bumped by the reset,
    so (subject, period, band) alone would collapse the two and the consumer would
    discard the new warning.

    So the identity of a crossing is (subject, period, band, arm generation), and
    re-sending it is the only thing that reproduces the same id.
    """
    payload = f"{subject_key}|{period_key}|{band}|{arm_seq}"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"evt_{digest[:32]}"


def _build_event(
    region: DBRegion, subject: _Subject, band: int, period_key: str, arm_seq: int
) -> BudgetAlertEvent:
    return BudgetAlertEvent(
        event_id=event_id_for(subject.subject_key, period_key, band, arm_seq),
        subject_type=subject.subject_type,
        subject_key=subject.subject_key,
        threshold_pct=band,
        percent_used=subject.percent_used,
        spend=round(subject.spend, 4),
        max_budget=round(float(subject.max_budget or 0.0), 4),
        region_id=region.id,
        region_name=region.name,
        period_key=period_key,
        period_start=subject.window.period_start,
        period_end=subject.window.period_end,
        budget_duration=subject.window.budget_duration,
        budget_source=subject.budget_source,
        team_id=subject.team.id if subject.team else None,
        team_name=subject.team.name if subject.team else None,
        user_id=subject.user.id if subject.user else None,
        user_email=subject.user.email if subject.user else None,
        key_id=subject.key.id if subject.key else None,
        key_name=subject.key.name if subject.key else None,
        is_service_key=(subject.key is not None and subject.key.owner_id is None),
    )


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #


def _upsert_state(
    db: Session,
    *,
    subject: _Subject,
    region_id: int,
    band: int,
    period_key: str,
    notified_at: datetime | None,
    bump_arm: bool = False,
) -> None:
    row = (
        db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key == subject.subject_key)
        .first()
    )
    if row is None:
        row = DBBudgetAlertState(
            subject_key=subject.subject_key,
            subject_type=subject.subject_type,
            region_id=region_id,
            arm_seq=0,
        )
        db.add(row)
    if bump_arm:
        # A band was re-armed, so the next crossing of it is a new event and must
        # not reuse the id of the one already sent.
        row.arm_seq = int(row.arm_seq or 0) + 1
    row.subject_type = subject.subject_type
    row.region_id = region_id
    row.team_id = subject.team.id if subject.team else None
    row.user_id = subject.user.id if subject.user else None
    row.key_id = subject.key.id if subject.key else None
    row.period_key = period_key
    row.last_threshold_pct = band
    row.spend_at_notify = round(subject.spend, 4)
    row.budget_at_notify = round(float(subject.max_budget or 0.0), 4)
    row.percent_at_notify = subject.percent_used
    if notified_at is not None:
        row.notified_at = notified_at


def apply_resets(db: Session, region: DBRegion, evaluation: RegionEvaluation) -> None:
    """Persist silent band/period rewrites. Safe to run before delivery."""
    for subject, band in evaluation.resets:
        _upsert_state(
            db,
            subject=subject,
            region_id=region.id,
            band=band,
            period_key=subject.window.period_key,
            notified_at=None,
            bump_arm=True,
        )
    if evaluation.resets:
        db.commit()


def mark_notified(
    db: Session,
    region: DBRegion,
    evaluation: RegionEvaluation,
    delivered_event_ids: set[str],
    now: datetime | None = None,
) -> int:
    """Advance state for the events that were actually delivered.

    Called *after* delivery on purpose. If the webhook fails, the band is left
    where it was and the next tick re-detects the same crossing, which gives
    at-least-once delivery without an outbox table. Consumers de-duplicate on
    ``event_id``.
    """
    now = now or datetime.now(UTC)
    by_subject = {
        event.subject_key: event
        for event in evaluation.events
        if event.event_id in delivered_event_ids
    }
    if not by_subject:
        return 0

    advanced = 0
    for subject in evaluation.subjects:
        event = by_subject.get(subject.subject_key)
        if event is None:
            continue
        _upsert_state(
            db,
            subject=subject,
            region_id=region.id,
            band=event.threshold_pct,
            period_key=event.period_key,
            notified_at=now,
        )
        advanced += 1
    db.commit()
    return advanced


def write_audit_logs(
    db: Session,
    events: list[BudgetAlertEvent],
    delivered_event_ids: set[str],
    now: datetime | None = None,
) -> int:
    """Record each attempted crossing in the audit log.

    ``budget_alert_state`` only holds the *current* band per subject, so without
    this there is no answer to "did we warn this customer before their keys were
    cut off" — a billing-dispute question that application logs (rotated, and
    absent from the DB) cannot settle. ``delivered`` distinguishes "the customer
    was told" from "we detected it but could not reach the consumer".

    An undelivered crossing is re-detected next tick, so a consumer outage
    produces one row per attempt. That repetition is the retry record, and it is
    bounded by the tick interval rather than by traffic.

    Audit failure must never fail the sweep: alerting is the job, auditing is the
    side effect.
    """
    if not events:
        return 0
    now = now or datetime.now(UTC)
    written = 0
    try:
        for event in events:
            db.add(
                DBAuditLog(
                    timestamp=now,
                    user_id=None,
                    event_type="WORKER",
                    resource_type=event.subject_type,
                    resource_id=event.subject_key,
                    action="budget.threshold_reached",
                    details={
                        "event_id": event.event_id,
                        "delivered": event.event_id in delivered_event_ids,
                        "threshold_percent": event.threshold_pct,
                        "percent_used": event.percent_used,
                        "spend": event.spend,
                        "max_budget": event.max_budget,
                        "region_id": event.region_id,
                        "region_name": event.region_name,
                        "team_id": event.team_id,
                        "user_id": event.user_id,
                        "key_id": event.key_id,
                        "period_key": event.period_key,
                    },
                    request_source=None,
                )
            )
            written += 1
        db.commit()
    except Exception as exc:
        logger.error("Failed to write budget alert audit logs: %s", exc)
        try:
            db.rollback()
        except Exception as rollback_exc:
            logger.warning(
                "Failed to roll back budget alert audit logs: %s", rollback_exc
            )
        return 0
    return written


# --------------------------------------------------------------------------- #
# Job entry point
# --------------------------------------------------------------------------- #


def regions_to_sweep(db: Session) -> list[DBRegion]:
    """Regions worth evaluating: any that still hold at least one live key.

    Deliberately NOT filtered on ``is_active``. That flag governs whether a
    region accepts *new* provisioning, not whether its existing keys still serve
    traffic. On PROD, region 5 (``amazeeai-de103``) is inactive yet holds 11,859
    keys — 85% of all keys, including the anonymous Drupal trial fleet, which is
    exactly the population that runs into budget limits. Filtering on
    ``is_active`` silently excluded them from every alert.

    Requiring a live key also keeps the sweep off decommissioned regions, whose
    LiteLLM URL may no longer resolve, without needing a second flag: an empty
    region has nothing to alert on regardless of its status.
    """
    return (
        db.query(DBRegion)
        .filter(
            DBRegion.litellm_api_url.isnot(None),
            DBRegion.litellm_api_url != "",
            DBRegion.litellm_api_key.isnot(None),
            DBRegion.litellm_api_key != "",
            db.query(DBPrivateAIKey.id)
            .filter(
                DBPrivateAIKey.region_id == DBRegion.id,
                DBPrivateAIKey.litellm_token.isnot(None),
            )
            .exists(),
        )
        .order_by(DBRegion.id)
        .all()
    )


@budget_alert_run_duration.time()
async def monitor_budget_thresholds(db: Session) -> dict[str, int]:
    """Sweep every active region, notify new crossings, and record what stuck.

    Regions are independent, so their LiteLLM reads are overlapped. They share one
    Session, so their DB work interleaves at every ``await`` — safe only because
    each query is synchronous (nothing else can run mid-query) and ``evaluate_region``
    never writes. Every write happens below, sequentially, after the gather. Adding
    a write inside ``evaluate_region`` would need a session per region.
    """
    from app.services.budget_alert_webhook import deliver_events

    thresholds = parse_thresholds()
    if not thresholds:
        logger.warning("No budget alert thresholds configured; nothing to do")
        return {"regions": 0, "events": 0, "delivered": 0}

    regions = regions_to_sweep(db)
    logger.info(
        "Budget threshold sweep starting: %d region(s), thresholds=%s, enabled=%s",
        len(regions),
        thresholds,
        settings.BUDGET_ALERT_ENABLED,
    )

    totals = {
        "regions": 0,
        "subjects": 0,
        "events": 0,
        "delivered": 0,
        "audited": 0,
        "litellm_calls": 0,
    }

    semaphore = asyncio.Semaphore(max(1, settings.BUDGET_ALERT_REGION_CONCURRENCY))

    async def _evaluate(region: DBRegion) -> tuple[DBRegion, RegionEvaluation | None]:
        async with semaphore:
            try:
                return region, await evaluate_region(db, region, thresholds=thresholds)
            except Exception as exc:
                logger.error(
                    "Budget threshold evaluation failed for region %s: %s",
                    region.name,
                    exc,
                    exc_info=True,
                )
                return region, None

    for region, evaluation in await asyncio.gather(
        *[_evaluate(region) for region in regions]
    ):
        if evaluation is None:
            continue
        totals["regions"] += 1
        totals["subjects"] += evaluation.subjects_evaluated
        totals["events"] += len(evaluation.events)
        totals["litellm_calls"] += evaluation.litellm_calls
        budget_alert_subjects_evaluated.labels(region_name=region.name).set(
            evaluation.subjects_evaluated
        )
        for event in evaluation.events:
            budget_alerts_fired_total.labels(
                scope=event.subject_type, threshold=str(event.threshold_pct)
            ).inc()

        # Silent rewrites are unconditional: they carry no notification, so
        # holding them back would only cause a stale band to suppress a later
        # genuine crossing.
        apply_resets(db, region, evaluation)

        if not evaluation.events:
            continue

        for event in evaluation.events:
            logger.info(
                "Budget threshold crossed: scope=%s subject=%s %.2f%% "
                "(spend=%.4f budget=%.4f) band=%d%% region=%s",
                event.subject_type,
                event.subject_key,
                event.percent_used,
                event.spend,
                event.max_budget,
                event.threshold_pct,
                event.region_name,
            )

        if not settings.BUDGET_ALERT_ENABLED:
            # Log-only mode: percentages can be validated against the spend API
            # before any third party is told anything. State is left untouched so
            # enabling delivery later still sends these crossings.
            continue

        delivered = await deliver_events(evaluation.events)
        totals["delivered"] += len(delivered)
        undelivered = len(evaluation.events) - len(delivered)
        if undelivered > 0:
            budget_alert_delivery_failures_total.inc(undelivered)
        # Audit before advancing state: if mark_notified were to fail, the record
        # that we did warn the customer should still survive.
        totals["audited"] += write_audit_logs(db, evaluation.events, delivered)
        mark_notified(db, region, evaluation, delivered)

    logger.info(
        "Budget threshold sweep finished: regions=%d subjects=%d events=%d "
        "delivered=%d audited=%d litellm_calls=%d",
        totals["regions"],
        totals["subjects"],
        totals["events"],
        totals["delivered"],
        totals["audited"],
        totals["litellm_calls"],
    )
    return totals
