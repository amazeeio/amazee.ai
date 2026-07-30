"""Tests for budget threshold alerts (AI-448).

The important cases here are the ones where the maths is easy to get wrong:
LiteLLM's compounding team counter, POOL's moving denominator, and cycle
rollover. A percentage that is silently wrong is worse than no alert at all,
because it teaches customers to ignore the warnings.

Note the split of responsibilities in the fakes below, which mirrors the engine:
daily-activity day-rows carry the *spend* (summed from the oldest still-valid
ledger entry), while key state carries only the *denominator*. LiteLLM's key spend
counters are lifetime totals a top-up never resets, so they are never the
numerator.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.budget_alert_service import (
    SUBJECT_KEY,
    SUBJECT_TEAM,
    SUBJECT_TEAM_MEMBER,
    apply_resets,
    evaluate_region,
    event_id_for,
    highest_crossed_band,
    mark_notified,
    parse_thresholds,
)
from app.core.config import settings
from app.core.spend_period_service import resolve_team_period_window
from app.db.models import (
    DBBudgetAlertState,
    DBLimitedResource,
    DBPeriodicBudgetLedgerEntry,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.schemas.limits import (
    LimitSource,
    LimitType,
    OwnerType,
    ResourceType,
    UnitType,
)
from app.schemas.models import BudgetType
from app.services.litellm import LiteLLMService

THRESHOLDS = [50, 75, 90, 100]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_parse_thresholds_sorts_dedupes_and_drops_junk():
    assert parse_thresholds("90, 50,75,50, ,abc,100") == [50, 75, 90, 100]


def test_parse_thresholds_rejects_out_of_range():
    assert parse_thresholds("0,-5,50,99999") == [50]


def test_highest_crossed_band_returns_only_the_top_band():
    # A jump straight past three bands must not produce three alerts.
    assert highest_crossed_band(95.0, THRESHOLDS) == 90
    assert highest_crossed_band(49.9, THRESHOLDS) == 0
    assert highest_crossed_band(50.0, THRESHOLDS) == 50
    assert highest_crossed_band(140.0, THRESHOLDS) == 100


# --------------------------------------------------------------------------- #
# Fixtures and fakes
# --------------------------------------------------------------------------- #


@pytest.fixture
def region(db):
    region = DBRegion(
        name="test-region",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="http://litellm.test",
        litellm_api_key="sk-test",
        is_active=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_team(db, budget_type=BudgetType.POOL, name="alerts-team"):
    team = DBTeam(
        name=name,
        admin_email=f"{name}@example.com",
        budget_type=budget_type,
        # Pool gating off keeps these teams on the plain POOL path; the gate is a
        # separate concern from budget percentage.
        require_purchase_for_requests=False,
        created_at=datetime.now(UTC) - timedelta(days=5),
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_key(db, team, region, *, name="k1", token="sk-key-1", owner=None):
    key = DBPrivateAIKey(
        name=name,
        litellm_token=token,
        litellm_api_url=region.litellm_api_url,
        region_id=region.id,
        team_id=team.id,
        owner_id=owner.id if owner else None,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


def _make_user(db, team, email="member@example.com"):
    user = DBUser(email=email, hashed_password="x", is_active=True, team_id=team.id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_subscription(db, team, region, *, amount_cents, days_left=20):
    now = datetime.now(UTC)
    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team.id,
        region_id=region.id,
        entry_type="subscription",
        amount_cents=amount_cents,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(days=31 - days_left),
        effective_period_start=now - timedelta(days=31 - days_left),
        effective_period_end=now + timedelta(days=days_left),
    )
    db.add(entry)
    db.commit()
    return entry


def _add_topup(
    db, team, region, *, amount_cents, purchased_days_ago=1, expires_at=None
):
    now = datetime.now(UTC)
    entry = DBPeriodicBudgetLedgerEntry(
        team_id=team.id,
        region_id=region.id,
        entry_type="topup",
        amount_cents=amount_cents,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(days=purchased_days_ago),
        expires_at=expires_at if expires_at is not None else now + timedelta(days=364),
    )
    db.add(entry)
    db.commit()
    return entry


def _day(lite_team_id, spend, *, days_ago=0, keys=None):
    """One UTC day of activity, optionally with a per-key breakdown."""
    return {
        "date": (datetime.now(UTC) - timedelta(days=days_ago)).date().isoformat(),
        "metrics": {"spend": spend},
        "breakdown": {
            "entities": {lite_team_id: {"metrics": {"spend": spend}}},
            "api_keys": {
                LiteLLMService.hash_token(token): {"metrics": {"spend": value}}
                for token, value in (keys or {}).items()
            },
        },
    }


def _active(lite_team_id, spend=0.0, *, keys=None, days_ago=0):
    """Today's activity for one team."""
    return [_day(lite_team_id, spend, days_ago=days_ago, keys=keys)]


def _key_state(token, *, max_budget=None, budget_duration="365d"):
    """One entry of LiteLLM's /key/list response.

    Carries the *denominator* only. Spend deliberately comes from the day-rows, so
    a test cannot accidentally assert against LiteLLM's lifetime counter.
    """
    return {
        "token": LiteLLMService.hash_token(token),
        "max_budget": max_budget,
        "budget_duration": budget_duration,
    }


def _patch_litellm(team_rows, team_keys=None):
    """Patch the two LiteLLM reads the engine makes."""
    return patch.multiple(
        "app.core.budget_alert_service.LiteLLMService",
        get_all_team_daily_activity=AsyncMock(return_value=team_rows),
        list_keys_for_team=AsyncMock(return_value=team_keys or []),
    )


# --------------------------------------------------------------------------- #
# Team scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pool_team_percentage_uses_ledger_not_litellm_max_budget(db, region):
    """The denominator is our ledger, and one crossing yields one event."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    # $92 spent of $100 -> 92% -> crosses the 90 band.
    with _patch_litellm(_active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    team_events = [e for e in result.events if e.subject_type == SUBJECT_TEAM]
    assert len(team_events) == 1
    event = team_events[0]
    assert event.threshold_pct == 90
    assert event.max_budget == 100.0
    assert event.spend == 92.0
    assert 91.9 < event.percent_used < 92.1
    assert event.team_id == team.id
    assert event.budget_source == "team_ledger"


@pytest.mark.asyncio
async def test_team_spend_is_the_sum_of_its_keys(db, region):
    """Team and key percentages must come from the same numbers."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    _make_key(db, team, region, token="sk-a", name="a")
    _make_key(db, team, region, token="sk-b", name="b")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-a": 60.0, "sk-b": 32.0}),
        [_key_state("sk-a"), _key_state("sk-b")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.spend == 92.0
    assert event.threshold_pct == 90


@pytest.mark.asyncio
async def test_team_total_comes_from_keys_not_the_entity_figure(db, region):
    """The team numerator is built from the per-key breakdown.

    Using the entity figure directly would attribute spend we cannot tie to any of
    our keys, so team and key percentages could disagree.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    rows = _active(lite, spend=99999.0, keys={"sk-a": 55.0})

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.spend == 55.0
    assert event.threshold_pct == 50


@pytest.mark.asyncio
async def test_team_cap_tightens_the_denominator(db, region):
    """An operator team cap lowers the budget, so the same spend crosses higher."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    db.add(
        DBSpendCap(scope="team", region_id=region.id, team_id=team.id, max_budget=50.0)
    )
    db.commit()
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    # $46 is 46% of $100 but 92% of the $50 cap.
    with _patch_litellm(_active(lite, keys={"sk-a": 46.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.max_budget == 50.0
    assert event.threshold_pct == 90


@pytest.mark.asyncio
async def test_no_budget_means_no_event(db, region):
    """A team with no ledger and no limit has nothing to be a percentage of."""
    team = _make_team(db)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 500.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


@pytest.mark.asyncio
async def test_soft_deleted_team_is_skipped(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    team.deleted_at = datetime.now(UTC)
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 99.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


@pytest.mark.asyncio
async def test_empty_activity_sweep_produces_nothing(db, region):
    """No traffic and no budget change -> no per-team key calls at all.

    This is the property that makes a 5-minute cadence affordable: a quiet region
    costs exactly one request.
    """
    _make_team(db)
    list_keys = AsyncMock(return_value=[])

    with patch.multiple(
        "app.core.budget_alert_service.LiteLLMService",
        get_all_team_daily_activity=AsyncMock(return_value=[]),
        list_keys_for_team=list_keys,
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []
    assert result.subjects_evaluated == 0
    assert result.litellm_calls == 1
    list_keys.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_entities_are_ignored(db, region):
    """LiteLLM's own bookkeeping team is not one of ours."""
    _make_team(db)

    with _patch_litellm(_active("litellm-dashboard", spend=100.0)):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


@pytest.mark.asyncio
async def test_periodic_team_is_out_of_scope(db, region):
    """PERIODIC is excluded: on PROD every PERIODIC key belongs to the anonymous
    trial team, which has no MOAD workspace and nobody to notify."""
    team = _make_team(db, budget_type=BudgetType.PERIODIC, name="periodic-team")
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 99.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


# --------------------------------------------------------------------------- #
# Top-up windows: which spend counts against the current budget
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_spend_against_an_expired_topup_is_not_counted(db, region):
    """The top-up-only case, and the reason spend is not read from key counters.

    A top-up never resets key spend (purchase_periodic_topup only raises the team
    max_budget), so LiteLLM's counters are lifetime totals. The denominator counts
    only unexpired entries. Taking spend from the counters would therefore leave an
    expired top-up's spend in the numerator after its money left the denominator:

        $100 expired (of which $80 spent) + $100 bought 10 days ago, $2 spent
        lifetime $82 / remaining $100 -> 82%   (wrong, would alert at 75)
        windowed  $2 / remaining $100 ->  2%   (right, no alert)
    """
    team = _make_team(db)
    now = datetime.now(UTC)
    # Old money, now expired: $100 purchased 100 days ago, $80 spent against it.
    _add_topup(
        db,
        team,
        region,
        amount_cents=10_000,
        purchased_days_ago=100,
        expires_at=now - timedelta(days=2),
    )
    # Current money: $100 purchased 10 days ago.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=10)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    rows = [
        _day(lite, 80.0, days_ago=90, keys={"sk-a": 80.0}),  # spent on the old money
        _day(lite, 2.0, days_ago=1, keys={"sk-a": 2.0}),  # spent on the new money
    ]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    # Only the $2 counts, so nothing crosses 50%.
    assert result.events == []


@pytest.mark.asyncio
async def test_topup_only_team_counts_from_the_last_purchase(db, region):
    """For a top-up-only team the cycle starts at the last top-up purchase.

    Spend from before it belongs to an earlier cycle and is not counted again,
    even when the older top-up is still valid and still funding the balance.
    """
    team = _make_team(db)
    # Both still valid: $100 bought 100 days ago, $100 bought 10 days ago.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=100)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=10)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    rows = [
        _day(lite, 85.0, days_ago=90, keys={"sk-a": 85.0}),  # previous cycle
        _day(lite, 120.0, days_ago=1, keys={"sk-a": 120.0}),  # this cycle
    ]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    # Only the $120 spent since the last purchase counts, against the $200 still on
    # the books -> 60%. Including the older $85 would read 102.5% and fire 100.
    assert event.spend == 120.0
    assert event.max_budget == 200.0
    assert event.threshold_pct == 50
    assert 59.9 < event.percent_used < 60.1


@pytest.mark.asyncio
async def test_subscription_cycle_wins_over_an_older_topup(db, region):
    """A team holding both is measured over its current subscription cycle.

    The budget is still the whole available balance — subscription remaining plus
    top-up remaining — but spend from before the cycle opened has already been
    settled into ``consumed_cents``, so counting it again would double count.
    """
    team = _make_team(db)
    # $100 subscription, cycle opened 11 days ago; plus a $100 top-up from before.
    _add_subscription(db, team, region, amount_cents=10_000, days_left=20)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=60)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    rows = [
        _day(lite, 70.0, days_ago=40, keys={"sk-a": 70.0}),  # before the cycle
        _day(lite, 105.0, days_ago=1, keys={"sk-a": 105.0}),  # inside the cycle
    ]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.spend == 105.0  # not 175.0
    assert event.max_budget == 200.0  # subscription + top-up remaining
    assert event.threshold_pct == 50
    assert event.budget_duration == "31d"


@pytest.mark.asyncio
async def test_new_topup_re_arms_the_bands(db, region):
    """Alerts are per budget cycle, and a purchase starts a new one."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=5)  # $100
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows = [_day(lite, 92.0, days_ago=1, keys={"sk-a": 92.0})]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    first_event = next(e for e in first.events if e.subject_type == SUBJECT_TEAM)
    assert first_event.threshold_pct == 90
    mark_notified(db, region, first, {e.event_id for e in first.events})

    # Buying more moves the pool window anchor, so period_key changes.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=0)  # +$100
    with _patch_litellm(rows, [_key_state("sk-a")]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    # $92 of $200 is 46%, below the band already sent -- silent walk-back, no event.
    assert [e for e in second.events if e.subject_type == SUBJECT_TEAM] == []
    apply_resets(db, region, second)
    state = (
        db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key == f"team:{team.id}:{region.id}")
        .one()
    )
    assert state.last_threshold_pct == 0
    assert state.period_key != first_event.period_key


# --------------------------------------------------------------------------- #
# Budget decreases with no recent traffic
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_expired_topup_raises_the_percentage(db, region):
    """A lapsing top-up shrinks the denominator, with no new traffic at all."""
    team = _make_team(db)
    now = datetime.now(UTC)
    # $100 still valid, purchased 20 days ago.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=20)
    # $200 that lapsed an hour ago -- was purchased *after* the valid one, so the
    # window start stays at 20 days ago and the $92 below still counts.
    _add_topup(
        db,
        team,
        region,
        amount_cents=20_000,
        purchased_days_ago=15,
        expires_at=now - timedelta(hours=1),
    )
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows = [_day(lite, 92.0, days_ago=10, keys={"sk-a": 92.0})]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    # Budget was $300, is now $100 -> $92 jumps from 31% to 92%.
    assert event.max_budget == 100.0
    assert event.spend == 92.0
    assert event.threshold_pct == 90


@pytest.mark.asyncio
async def test_recent_cap_change_rechecks_a_silent_team(db, region):
    """Lowering a cap shrinks the denominator as effectively as an expiry."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)  # $1000
    _make_key(db, team, region, token="sk-a")
    db.add(
        DBSpendCap(
            scope="team",
            region_id=region.id,
            team_id=team.id,
            max_budget=50.0,
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        [_day(lite, 48.0, days_ago=3, keys={"sk-a": 48.0})], [_key_state("sk-a")]
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.max_budget == 50.0
    assert event.threshold_pct == 90


def test_old_expiry_does_not_recheck_forever(db, region):
    """The recheck window is bounded, so it cannot grow into a full table scan."""
    from app.core.budget_alert_service import denominator_change_candidates

    team = _make_team(db)
    _add_topup(
        db,
        team,
        region,
        amount_cents=5_000,
        purchased_days_ago=400,
        expires_at=datetime.now(UTC) - timedelta(days=35),
    )

    assert denominator_change_candidates(db, region.id, datetime.now(UTC)) == set()


def test_future_expiry_is_not_a_candidate_yet(db, region):
    """Nothing has changed until the entry actually lapses."""
    from app.core.budget_alert_service import denominator_change_candidates

    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=5_000)

    assert denominator_change_candidates(db, region.id, datetime.now(UTC)) == set()


def test_spend_window_start_is_the_last_topup_for_a_topup_only_team(db, region):
    """Directly pin the rule, since everything downstream depends on it."""
    from app.core.budget_alert_service import spend_window_start

    team = _make_team(db)
    now = datetime.now(UTC)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=40)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=5)

    window = resolve_team_period_window(db, team, region.id, now=now)
    start = spend_window_start(window, now)
    # The most recent purchase opens the cycle, not the older still-valid one.
    assert 4 <= (now - start).days <= 6


def test_spend_window_start_is_the_cycle_start_when_subscribed(db, region):
    """A subscription defines the cycle, whatever top-ups sit alongside it."""
    from app.core.budget_alert_service import spend_window_start

    team = _make_team(db)
    now = datetime.now(UTC)
    # Cycle opened 11 days ago (31d window, 20 days left).
    _add_subscription(db, team, region, amount_cents=10_000, days_left=20)
    # An older top-up must not drag the window back before the cycle.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=60)

    window = resolve_team_period_window(db, team, region.id, now=now)
    start = spend_window_start(window, now)
    assert 10 <= (now - start).days <= 12
    assert window.source == "subscription_ledger"


def test_spend_window_start_falls_back_to_team_creation(db, region):
    """A team with no ledger at all still needs a defined window."""
    from app.core.budget_alert_service import spend_window_start

    team = _make_team(db)
    now = datetime.now(UTC)
    window = resolve_team_period_window(db, team, region.id, now=now)
    start = spend_window_start(window, now)
    assert 4 <= (now - start).days <= 6


# --------------------------------------------------------------------------- #
# Which regions get swept
# --------------------------------------------------------------------------- #


def test_inactive_region_holding_keys_is_still_swept(db, region):
    """PROD region 5 is inactive but holds 85% of all keys.

    is_active governs new provisioning, not whether existing keys serve traffic.
    Filtering on it would exclude the anonymous trial fleet -- the population most
    likely to hit a budget limit -- from every alert.
    """
    from app.core.budget_alert_service import regions_to_sweep

    team = _make_team(db)
    region.is_active = False
    db.commit()
    _make_key(db, team, region, token="sk-inactive-region-key")

    assert [r.id for r in regions_to_sweep(db)] == [region.id]


def test_region_without_keys_is_not_swept(db, region):
    """An empty region has nothing to alert on, active or not."""
    from app.core.budget_alert_service import regions_to_sweep

    assert regions_to_sweep(db) == []


def test_region_without_litellm_credentials_is_not_swept(db, region):
    """A region we cannot reach must not be attempted every 5 minutes."""
    from app.core.budget_alert_service import regions_to_sweep

    team = _make_team(db)
    _make_key(db, team, region, token="sk-no-creds")
    region.litellm_api_url = ""
    db.commit()

    assert regions_to_sweep(db) == []


# --------------------------------------------------------------------------- #
# Dedup, re-arm, and the moving denominator
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_band_is_not_notified_twice(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows, keys = _active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]

    with _patch_litellm(rows, keys):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    mark_notified(db, region, first, {e.event_id for e in first.events})

    with _patch_litellm(rows, keys):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in second.events if e.subject_type == SUBJECT_TEAM] == []


@pytest.mark.asyncio
async def test_higher_band_notifies_again_in_the_same_period(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 60.0}), [_key_state("sk-a")]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    assert (
        next(e for e in first.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 50
    )
    mark_notified(db, region, first, {e.event_id for e in first.events})

    with _patch_litellm(_active(lite, keys={"sk-a": 91.0}), [_key_state("sk-a")]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert (
        next(e for e in second.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 90
    )


@pytest.mark.asyncio
async def test_new_period_re_arms_the_same_band(db, region):
    team = _make_team(db)
    subscription = _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows, keys = _active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]

    with _patch_litellm(rows, keys):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    mark_notified(db, region, first, {e.event_id for e in first.events})
    old_period_key = next(
        e for e in first.events if e.subject_type == SUBJECT_TEAM
    ).period_key

    # Roll the cycle: a new window means a new period_key.
    now = datetime.now(UTC)
    subscription.effective_period_start = now - timedelta(days=1)
    subscription.effective_period_end = now + timedelta(days=30)
    db.commit()

    with _patch_litellm(rows, keys):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in second.events if e.subject_type == SUBJECT_TEAM)
    assert event.threshold_pct == 90
    assert event.period_key != old_period_key


@pytest.mark.asyncio
async def test_failed_delivery_leaves_state_unadvanced_and_retries(db, region):
    """No outbox: an undelivered event must be re-detected next tick."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows, keys = _active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]

    with _patch_litellm(rows, keys):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    assert len(first.events) >= 1

    # Delivery failed -> nothing is marked.
    assert mark_notified(db, region, first, set()) == 0

    with _patch_litellm(rows, keys):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert (
        next(e for e in second.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 90
    )


# --------------------------------------------------------------------------- #
# Key scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_key_cap_drives_key_scope_alert(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)  # big team budget
    key = _make_key(db, team, region, token="sk-a")
    db.add(DBSpendCap(scope="key", region_id=region.id, key_id=key.id, max_budget=10.0))
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 7.6}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = [e for e in result.events if e.subject_type == SUBJECT_KEY]
    assert len(key_events) == 1
    assert key_events[0].key_id == key.id
    assert key_events[0].max_budget == 10.0
    assert key_events[0].threshold_pct == 75
    assert key_events[0].budget_source == "key_cap"


@pytest.mark.asyncio
async def test_service_key_and_user_key_both_alert(db, region):
    """A key is in scope for having a team, not for having an owner.

    PROD POOL teams hold 405 service keys (no owner) and 929 user keys; both must
    produce key-scope alerts, and the event says which it is so the consumer can
    route it to the workspace or to the individual.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    user = _make_user(db, team, email="both@example.com")
    svc = _make_key(db, team, region, token="sk-svc", name="svc")  # owner_id None
    usr = _make_key(db, team, region, token="sk-usr", name="usr", owner=user)
    for k in (svc, usr):
        db.add(
            DBSpendCap(scope="key", region_id=region.id, key_id=k.id, max_budget=10.0)
        )
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-svc": 9.5, "sk-usr": 9.5}),
        [_key_state("sk-svc"), _key_state("sk-usr")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    by_key = {e.key_id: e for e in result.events if e.subject_type == SUBJECT_KEY}
    assert set(by_key) == {svc.id, usr.id}
    assert by_key[svc.id].is_service_key is True
    assert by_key[usr.id].is_service_key is False
    assert all(e.threshold_pct == 90 for e in by_key.values())


@pytest.mark.asyncio
async def test_uncapped_key_covered_by_the_team_alert_on_a_single_key_team(db, region):
    """An uncapped key has no key-level budget, but the customer is still warned.

    44.7% of team-attached keys on PROD carry no max_budget: for POOL, only an
    explicit cap sets one, and everything else is bounded by the team pool. On a
    one-key team the key percentage would equal the team's, so only the team event
    fires -- the spend is routed, not dropped.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)  # $1000
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 900.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []
    team_event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert team_event.spend == 900.0
    assert team_event.max_budget == 1000.0
    assert team_event.threshold_pct == 90


@pytest.mark.asyncio
async def test_uncapped_key_measured_against_pool_when_team_has_several(db, region):
    """With more than one key, an uncapped key is measured against the pool.

    Legitimate rather than a proxy: worker.py leaves uncapped POOL keys with
    max_budget=None and enforces at team level, so the pool *is* that key's limit.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100 pool
    hog = _make_key(db, team, region, token="sk-hog", name="hog")
    _make_key(db, team, region, token="sk-quiet", name="quiet")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-hog": 92.0, "sk-quiet": 1.0}),
        [_key_state("sk-hog"), _key_state("sk-quiet")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = {e.key_id: e for e in result.events if e.subject_type == SUBJECT_KEY}
    # The hog crossed 90% of the pool; the quiet key crossed nothing.
    assert set(key_events) == {hog.id}
    assert key_events[hog.id].max_budget == 100.0
    assert key_events[hog.id].budget_source == "team_pool"
    assert key_events[hog.id].threshold_pct == 90


@pytest.mark.asyncio
async def test_distributed_burn_is_team_scope_only(db, region):
    """Three keys at ~31% each trip the team but no individual key.

    With a shared pool there is no per-key line to cross, so per-key events are
    attribution on top of the team alert rather than independent coverage.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    for name in ("a", "b", "c"):
        _make_key(db, team, region, token=f"sk-{name}", name=name)
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={f"sk-{n}": 31.0 for n in ("a", "b", "c")}),
        [_key_state(f"sk-{n}") for n in ("a", "b", "c")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []
    assert (
        next(e for e in result.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 90
    )


@pytest.mark.asyncio
async def test_zero_budget_key_is_skipped(db, region):
    """A pool-gated key is born with max_budget 0; that is not 100% used."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    _make_key(db, team, region, token="sk-a", name="a")
    _make_key(db, team, region, token="sk-b", name="b")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-a": 0.0, "sk-b": 1.0}),
        [_key_state("sk-a", max_budget=0.0), _key_state("sk-b")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []


@pytest.mark.asyncio
async def test_expiring_key_is_skipped(db, region):
    """A key being expired (budget_duration 0d) is not a budget warning."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    key = _make_key(db, team, region, token="sk-a")
    db.add(
        DBSpendCap(scope="key", region_id=region.id, key_id=key.id, max_budget=100.0)
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-a": 95.0}),
        [_key_state("sk-a", max_budget=100.0, budget_duration="0d")],
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []
    # Its spend is excluded from the team total too -- the key is going away.
    assert [e for e in result.events if e.subject_type == SUBJECT_TEAM] == []


@pytest.mark.asyncio
async def test_litellm_key_budget_used_when_no_db_cap_exists(db, region):
    """With no spend_caps row, whatever LiteLLM enforces is the key's budget."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    key = _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-a": 51.0}), [_key_state("sk-a", max_budget=100.0)]
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = [e for e in result.events if e.subject_type == SUBJECT_KEY]
    assert len(key_events) == 1
    assert key_events[0].key_id == key.id
    assert key_events[0].max_budget == 100.0
    assert key_events[0].threshold_pct == 50
    assert key_events[0].budget_source == "litellm_key_budget"


@pytest.mark.asyncio
async def test_key_without_team_id_is_out_of_scope(db, region):
    """Only keys carrying both a team and a region are in scope (MOAD's shape)."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    user = _make_user(db, team, email="orphan@example.com")
    key = _make_key(db, team, region, token="sk-orphan", owner=user)
    key.team_id = None  # owner only, no team
    db.commit()
    db.add(DBSpendCap(scope="key", region_id=region.id, key_id=key.id, max_budget=1.0))
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(
        _active(lite, keys={"sk-orphan": 5.0}), [_key_state("sk-orphan")]
    ):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []


# --------------------------------------------------------------------------- #
# Team-member scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_team_member_cap_drives_member_alert(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    user = _make_user(db, team)
    _make_key(db, team, region, token="sk-a", owner=user)
    db.add(
        DBSpendCap(
            scope="team_member",
            region_id=region.id,
            team_id=team.id,
            user_id=user.id,
            max_budget=20.0,
        )
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 19.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    member_events = [e for e in result.events if e.subject_type == SUBJECT_TEAM_MEMBER]
    assert len(member_events) == 1
    assert member_events[0].user_id == user.id
    assert member_events[0].max_budget == 20.0
    assert member_events[0].threshold_pct == 90


@pytest.mark.asyncio
async def test_member_without_cap_emits_nothing(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    user = _make_user(db, team, email="nocap@example.com")
    _make_key(db, team, region, token="sk-a", owner=user)
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 900.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_TEAM_MEMBER] == []


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_audit_log_records_delivered_and_undelivered_crossings(db, region):
    """budget_alert_state only holds the current band, so the audit log is the
    only durable answer to "was this customer warned"."""
    from app.core.budget_alert_service import write_audit_logs
    from app.db.models import DBAuditLog

    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)

    # Delivered.
    assert write_audit_logs(db, [event], {event.event_id}) == 1
    row = (
        db.query(DBAuditLog)
        .filter(DBAuditLog.action == "budget.threshold_reached")
        .one()
    )
    assert row.resource_type == SUBJECT_TEAM
    assert row.resource_id == f"team:{team.id}:{region.id}"
    assert row.details["delivered"] is True
    assert row.details["threshold_percent"] == 90
    assert row.details["team_id"] == team.id
    assert row.event_type == "WORKER"

    # Undelivered crossings are recorded too, flagged as such.
    write_audit_logs(db, [event], set())
    rows = (
        db.query(DBAuditLog)
        .filter(DBAuditLog.action == "budget.threshold_reached")
        .all()
    )
    assert sorted(r.details["delivered"] for r in rows) == [False, True]


def test_audit_log_write_failure_does_not_raise(db):
    """Auditing is a side effect; it must never break the sweep."""
    from app.core.budget_alert_service import BudgetAlertEvent, write_audit_logs

    event = BudgetAlertEvent(
        event_id="evt_audit",
        subject_type=SUBJECT_TEAM,
        subject_key="team:1:1",
        threshold_pct=50,
        percent_used=55.0,
        spend=55.0,
        max_budget=100.0,
        region_id=1,
        region_name="r",
        period_key="k",
        period_start=None,
        period_end=None,
        budget_duration=None,
    )
    with patch.object(db, "commit", side_effect=RuntimeError("db gone")):
        assert write_audit_logs(db, [event], set()) == 0


# --------------------------------------------------------------------------- #
# Delivery payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_webhook_payload_excludes_the_litellm_token(db, region):
    from app.services.budget_alert_webhook import serialize_event

    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with _patch_litellm(_active(lite, keys={"sk-a": 92.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    payload = serialize_event(
        next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    )
    assert payload["type"] == "budget.threshold_reached"
    assert payload["data"]["threshold_percent"] == 90
    assert payload["data"]["team"]["id"] == team.id
    assert payload["data"]["budget_source"] == "team_ledger"
    assert "sk-" not in str(payload)
    assert "token" not in payload["data"]


@pytest.mark.asyncio
async def test_delivery_failure_returns_no_delivered_ids(db, region):
    """A non-2xx must not be reported as delivered, or the alert is lost."""
    from app.core.budget_alert_service import BudgetAlertEvent
    from app.services.budget_alert_webhook import deliver_events

    event = BudgetAlertEvent(
        event_id="evt_test",
        subject_type=SUBJECT_TEAM,
        subject_key="team:1:1",
        threshold_pct=90,
        percent_used=92.0,
        spend=92.0,
        max_budget=100.0,
        region_id=region.id,
        region_name=region.name,
        period_key="subscription_ledger:x",
        period_start=None,
        period_end=None,
        budget_duration="31d",
        team_id=1,
    )

    class _Resp:
        status_code = 500
        text = "boom"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

    with patch.object(settings, "BUDGET_ALERT_WEBHOOK_URL", "http://moad.test/hook"):
        with patch("app.services.budget_alert_webhook.httpx.AsyncClient", _Client):
            delivered = await deliver_events([event])

    assert delivered == set()


@pytest.mark.asyncio
async def test_delivery_without_url_configured_is_a_no_op(db, region):
    from app.core.budget_alert_service import BudgetAlertEvent
    from app.services.budget_alert_webhook import deliver_events

    event = BudgetAlertEvent(
        event_id="evt_test2",
        subject_type=SUBJECT_TEAM,
        subject_key="team:1:1",
        threshold_pct=50,
        percent_used=55.0,
        spend=55.0,
        max_budget=100.0,
        region_id=region.id,
        region_name=region.name,
        period_key="k",
        period_start=None,
        period_end=None,
        budget_duration=None,
    )
    with patch.object(settings, "BUDGET_ALERT_WEBHOOK_URL", ""):
        assert await deliver_events([event]) == set()


# --------------------------------------------------------------------------- #
# Retry identity and lookback coverage
# --------------------------------------------------------------------------- #


def test_event_id_identifies_the_crossing_not_the_attempt():
    """The same crossing must always produce the same id.

    Delivery is at-least-once: if a response is lost after the consumer accepted
    the batch, the next tick re-sends. A random id would defeat the consumer's
    de-duplication and the customer would be notified twice.
    """
    first = event_id_for("team:7:2", "2026-07-01:2026-08-01", 90, 0)
    second = event_id_for("team:7:2", "2026-07-01:2026-08-01", 90, 0)
    assert first == second
    assert first.startswith("evt_")

    # A different band, period, subject or arming is a different crossing.
    assert event_id_for("team:7:2", "2026-07-01:2026-08-01", 100, 0) != first
    assert event_id_for("team:7:2", "2026-08-01:2026-09-01", 90, 0) != first
    assert event_id_for("team:8:2", "2026-07-01:2026-08-01", 90, 0) != first
    assert event_id_for("team:7:2", "2026-07-01:2026-08-01", 90, 1) != first


@pytest.mark.asyncio
async def test_undelivered_event_keeps_its_id_on_the_next_tick(db, region):
    """A failed delivery is retried under the original id, so it dedups."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    rows = _active(lite, keys={"sk-a": 92.0})

    with _patch_litellm(rows, [_key_state("sk-a")]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    # Nothing is marked notified: this stands for a POST that did not confirm.
    with _patch_litellm(rows, [_key_state("sk-a")]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    ids_first = {e.event_id for e in first.events}
    assert ids_first == {e.event_id for e in second.events}
    assert len(ids_first) == len(first.events)  # ids are unique within a tick


@pytest.mark.asyncio
async def test_sweep_reaches_back_to_the_oldest_valid_purchase(db, region):
    """The request window is derived from the ledger, not from a fixed span.

    A top-up bought 200 days ago is still valid, so its spend counts. Asking
    LiteLLM for a shorter span would return part of the spend and understate the
    percentage for as long as the entry lives.
    """
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=200)
    _make_key(db, team, region, token="sk-a")

    activity = AsyncMock(return_value=[])
    with patch.multiple(
        "app.core.budget_alert_service.LiteLLMService",
        get_all_team_daily_activity=activity,
        list_keys_for_team=AsyncMock(return_value=[]),
    ):
        await evaluate_region(db, region, thresholds=THRESHOLDS)

    start = date.fromisoformat(activity.await_args.args[0])
    expected = (datetime.now(UTC) - timedelta(days=200)).date()
    assert start == expected


@pytest.mark.asyncio
async def test_sweep_does_not_reach_past_the_lookback_floor(db, region):
    """The floor still bounds the request, however old the ledger is."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=200)
    _make_key(db, team, region, token="sk-a")

    activity = AsyncMock(return_value=[])
    with patch.object(settings, "BUDGET_ALERT_MAX_LOOKBACK_DAYS", 30):
        with patch.multiple(
            "app.core.budget_alert_service.LiteLLMService",
            get_all_team_daily_activity=activity,
            list_keys_for_team=AsyncMock(return_value=[]),
        ):
            await evaluate_region(db, region, thresholds=THRESHOLDS)

    start = date.fromisoformat(activity.await_args.args[0])
    assert start == (datetime.now(UTC) - timedelta(days=30)).date()


@pytest.mark.asyncio
async def test_window_before_the_floor_is_back_filled_not_dropped(db, region):
    """A team the wide sweep cannot cover gets its own scoped call.

    The wide request is bounded by the floor, so its rows hold only part of a
    200-day window. Summing them would peg the team at 30% for good and it would
    never reach the 90 band. Skipping it instead would hide every crossing until
    someone raised the floor by hand, so its spend is fetched over its own window.
    """
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=200)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    # The wide sweep only sees the recent $30 of a $92 window.
    wide_rows = _active(lite, keys={"sk-a": 30.0})
    scoped_rows = [
        _day(lite, 62.0, days_ago=150, keys={"sk-a": 62.0}),
        _day(lite, 30.0, days_ago=0, keys={"sk-a": 30.0}),
    ]
    scoped = AsyncMock(return_value=scoped_rows)

    with patch.object(settings, "BUDGET_ALERT_MAX_LOOKBACK_DAYS", 30):
        with patch.multiple(
            "app.core.budget_alert_service.LiteLLMService",
            get_all_team_daily_activity=AsyncMock(return_value=wide_rows),
            get_team_daily_activity=scoped,
            list_keys_for_team=AsyncMock(return_value=[_key_state("sk-a")]),
        ):
            result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    # The scoped call asked for the team's own window, not the floor.
    assert scoped.await_args.args[0] == lite
    assert (
        date.fromisoformat(scoped.await_args.args[1])
        == (datetime.now(UTC) - timedelta(days=200)).date()
    )

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.spend == 92.0  # the whole window, not just the visible $30
    assert event.threshold_pct == 90


@pytest.mark.asyncio
async def test_team_is_dropped_when_the_back_fill_fails(db, region):
    """If the exact number cannot be fetched, report none rather than a wrong one."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=200)
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    with patch.object(settings, "BUDGET_ALERT_MAX_LOOKBACK_DAYS", 30):
        with patch.multiple(
            "app.core.budget_alert_service.LiteLLMService",
            get_all_team_daily_activity=AsyncMock(
                return_value=_active(lite, keys={"sk-a": 30.0})
            ),
            get_team_daily_activity=AsyncMock(side_effect=RuntimeError("boom")),
            list_keys_for_team=AsyncMock(return_value=[_key_state("sk-a")]),
        ):
            result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []
    assert result.subjects_evaluated == 0


@pytest.mark.asyncio
async def test_budget_less_team_costs_no_extra_call(db, region):
    """The 673 ledger-less POOL teams must not each trigger a back-fill.

    They anchor on team.created_at, which is routinely older than the window, but
    they have no budget to be a percentage of, so there is nothing to fetch.
    """
    team = _make_team(db)  # no ledger entry at all
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    scoped = AsyncMock(return_value=[])

    with patch.object(settings, "BUDGET_ALERT_MAX_LOOKBACK_DAYS", 1):
        with patch.multiple(
            "app.core.budget_alert_service.LiteLLMService",
            get_all_team_daily_activity=AsyncMock(
                return_value=_active(lite, keys={"sk-a": 5.0})
            ),
            get_team_daily_activity=scoped,
            list_keys_for_team=AsyncMock(return_value=[_key_state("sk-a")]),
        ):
            result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    scoped.assert_not_awaited()
    assert result.events == []


@pytest.mark.asyncio
async def test_pool_team_budget_limit_is_not_a_pool_denominator(db, region):
    """A POOL team's budget is the money it bought, not its BUDGET limit.

    673 POOL teams on PROD hold no valid ledger entry but do carry a
    ``limited_resources`` BUDGET row (mostly $27). That row is a provisioning
    allowance, not available credit, and with no purchase to anchor on the spend
    window would stretch back to team creation. Measuring against it would invent
    a percentage, so such a team gets no team event.
    """
    team = _make_team(db)
    db.add(
        DBLimitedResource(
            owner_type=OwnerType.TEAM,
            owner_id=team.id,
            resource=ResourceType.BUDGET,
            limit_type=LimitType.DATA_PLANE,
            unit=UnitType.DOLLAR,
            max_value=27.0,
            current_value=0.0,
            limited_by=LimitSource.MANUAL,
            set_by="test",
        )
    )
    db.commit()
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"

    # Spend that would be 92% of the $27 limit if it were treated as a budget.
    with _patch_litellm(_active(lite, keys={"sk-a": 25.0}), [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_TEAM] == []


@pytest.mark.asyncio
async def test_periodic_team_still_uses_its_budget_limit(db, region):
    """The limit fallback stays available to PERIODIC teams, which is its purpose."""
    from app.core.budget_alert_service import _team_budget

    team = _make_team(db, budget_type=BudgetType.PERIODIC, name="periodic-team")
    db.add(
        DBLimitedResource(
            owner_type=OwnerType.TEAM,
            owner_id=team.id,
            resource=ResourceType.BUDGET,
            limit_type=LimitType.DATA_PLANE,
            unit=UnitType.DOLLAR,
            max_value=27.0,
            current_value=0.0,
            limited_by=LimitSource.MANUAL,
            set_by="test",
        )
    )
    db.commit()

    assert _team_budget(db, team, region.id) == 27.0


@pytest.mark.asyncio
async def test_re_crossing_a_band_in_one_period_gets_a_new_event_id(db, region):
    """A re-armed band must not reuse the id of the crossing already sent.

    A subscription-backed POOL team keeps one ``period_key`` for the whole
    subscription window, so a top-up inside it lowers the percentage without
    starting a new period. The band is silently re-armed, and when spend climbs
    back through it that is a real second warning. Keyed on
    (subject, period, band) alone the two crossings would share an id and the
    consumer would discard the second as a duplicate.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000, days_left=20)  # $100
    _make_key(db, team, region, token="sk-a")
    lite = f"{region.name}_{team.id}"
    subject_key = f"team:{team.id}:{region.id}"

    # 1. $92 of $100 -> crosses 90, delivered.
    rows = [_day(lite, 92.0, days_ago=2, keys={"sk-a": 92.0})]
    with _patch_litellm(rows, [_key_state("sk-a")]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    first_event = next(e for e in first.events if e.subject_type == SUBJECT_TEAM)
    assert first_event.threshold_pct == 90
    mark_notified(db, region, first, {e.event_id for e in first.events})

    # 2. A top-up doubles the pool: $92 of $200 is 46%, so the band walks back
    #    silently. The subscription still defines the period.
    _add_topup(db, team, region, amount_cents=10_000, purchased_days_ago=0)
    with _patch_litellm(rows, [_key_state("sk-a")]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)
    assert [e for e in second.events if e.subject_type == SUBJECT_TEAM] == []
    apply_resets(db, region, second)

    state = (
        db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key == subject_key)
        .one()
    )
    assert state.period_key == first_event.period_key  # same subscription period
    assert state.arm_seq == 1  # the reset re-armed the bands

    # 3. Spend climbs to $185 of $200 -> crosses 90 again, in the same period.
    rows_after = [
        _day(lite, 92.0, days_ago=2, keys={"sk-a": 92.0}),
        _day(lite, 93.0, days_ago=0, keys={"sk-a": 93.0}),
    ]
    with _patch_litellm(rows_after, [_key_state("sk-a")]):
        third = await evaluate_region(db, region, thresholds=THRESHOLDS)

    third_event = next(e for e in third.events if e.subject_type == SUBJECT_TEAM)
    assert third_event.threshold_pct == 90
    assert third_event.period_key == first_event.period_key
    # Same subject, same period, same band -- but a genuinely new warning.
    assert third_event.event_id != first_event.event_id


# --------------------------------------------------------------------------- #
# Caps are per cycle, so they are measured over their own cycle
# --------------------------------------------------------------------------- #


def test_current_cycle_start_rolls_forward_to_contain_now():
    """A stale anchor must not hand back a window that already closed.

    PROD has keys whose LiteLLM budget_reset_at is a month in the past, so the
    cycle has to be stepped forward the way LiteLLM steps it on reset.
    """
    from app.core.spend_period_service import current_cycle_start

    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    anchor = datetime(2026, 1, 1, tzinfo=UTC)  # 210 days back

    start = current_cycle_start("31d", anchor, now)
    assert start is not None
    # 210 // 31 = 6 whole cycles -> 186 days after the anchor.
    assert start == anchor + timedelta(days=186)
    assert start <= now < start + timedelta(days=31)

    # 1mo snaps to the calendar month, matching LiteLLM's own boundary.
    assert current_cycle_start("1mo", anchor, now) == datetime(2026, 7, 1, tzinfo=UTC)
    # An anchor in the future is left alone, and junk yields nothing.
    future = now + timedelta(days=5)
    assert current_cycle_start("31d", future, now) == future
    assert current_cycle_start("weekly", anchor, now) is None
    assert current_cycle_start(None, anchor, now) is None


@pytest.mark.asyncio
async def test_capped_key_is_measured_over_the_cap_cycle_not_the_pool(db, region):
    """195 of 196 key caps on PROD sit on top-up-only teams.

    The team's cycle can be far longer than the cap's, so summing the team window
    against a monthly cap would read hundreds of percent and fire immediately.
    """
    team = _make_team(db)
    # Top-up-only team whose cycle opened 90 days ago, holding $250.
    _add_topup(db, team, region, amount_cents=25_000, purchased_days_ago=90)
    key = _make_key(db, team, region, token="sk-a")
    db.add(
        DBSpendCap(
            scope="key",
            region_id=region.id,
            team_id=team.id,
            key_id=key.id,
            max_budget=100.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    # $200 spent 60 days ago (an earlier cap cycle) and $30 spent today.
    rows = [
        _day(lite, 200.0, days_ago=60, keys={"sk-a": 200.0}),
        _day(lite, 30.0, days_ago=0, keys={"sk-a": 30.0}),
    ]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = [e for e in result.events if e.subject_type == SUBJECT_KEY]
    # $30 of the $100 monthly cap is 30% -> no band. Measured over the team's
    # 90-day window it would have been $230/$100 = 230% and fired 100.
    assert key_events == []

    # The team subject still uses the team cycle and sees everything: $230 of the
    # $250 pool is 92%, so the team is warned even though the key is not.
    team_event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert team_event.spend == 230.0
    assert team_event.threshold_pct == 90


@pytest.mark.asyncio
async def test_capped_key_crossing_inside_its_own_cycle_still_fires(db, region):
    """The narrower window must not suppress a real crossing."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=100_000, purchased_days_ago=90)
    key = _make_key(db, team, region, token="sk-a")
    db.add(
        DBSpendCap(
            scope="key",
            region_id=region.id,
            team_id=team.id,
            key_id=key.id,
            max_budget=100.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    # $92 spent today, inside the current calendar month.
    rows = [_day(lite, 92.0, days_ago=0, keys={"sk-a": 92.0})]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_KEY)
    assert event.threshold_pct == 90
    assert event.spend == 92.0
    assert event.max_budget == 100.0
    assert event.budget_source == "key_cap"
    assert event.budget_duration == "1mo"
    # The cap's cycle, not the team's, so a new month re-arms the key alert.
    assert event.period_key.startswith("key_cap:1mo:")


@pytest.mark.asyncio
async def test_member_cap_is_measured_over_the_cap_cycle(db, region):
    """All 38 team-member caps on PROD are 1mo, same rule as key caps."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=100_000, purchased_days_ago=90)
    user = _make_user(db, team)
    _make_key(db, team, region, token="sk-a", owner=user)
    db.add(
        DBSpendCap(
            scope="team_member",
            region_id=region.id,
            team_id=team.id,
            user_id=user.id,
            max_budget=50.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    lite = f"{region.name}_{team.id}"

    rows = [
        _day(lite, 300.0, days_ago=60, keys={"sk-a": 300.0}),  # earlier cycle
        _day(lite, 26.0, days_ago=0, keys={"sk-a": 26.0}),  # this cycle
    ]

    with _patch_litellm(rows, [_key_state("sk-a")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM_MEMBER)
    # $26 of $50 is 52% -> crosses 50 only. Over the team window it would have
    # been $326/$50 and fired 100 on spend that belongs to earlier months.
    assert event.spend == 26.0
    assert event.threshold_pct == 50


@pytest.mark.asyncio
async def test_uncapped_key_keeps_the_team_cycle(db, region):
    """A key bounded by the pool has no cycle of its own."""
    team = _make_team(db)
    _add_topup(db, team, region, amount_cents=20_000, purchased_days_ago=90)
    _make_key(db, team, region, token="sk-a", name="a")
    _make_key(db, team, region, token="sk-b", name="b")
    lite = f"{region.name}_{team.id}"

    rows = [
        _day(lite, 100.0, days_ago=60, keys={"sk-a": 100.0}),
        _day(lite, 90.0, days_ago=0, keys={"sk-a": 90.0}),
    ]

    with _patch_litellm(rows, [_key_state("sk-a"), _key_state("sk-b")]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(
        e
        for e in result.events
        if e.subject_type == SUBJECT_KEY and e.budget_source == "team_pool"
    )
    # Both days count: the pool window is the team's, opened by the last top-up.
    assert event.spend == 190.0
    assert event.max_budget == 200.0
    assert event.threshold_pct == 90
