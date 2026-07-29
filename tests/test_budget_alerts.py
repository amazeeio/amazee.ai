"""Tests for budget threshold alerts (AI-448).

The important cases here are the ones where the maths is easy to get wrong:
LiteLLM's compounding team counter, POOL's moving denominator, and cycle
rollover. A percentage that is silently wrong is worse than no alert at all,
because it teaches customers to ignore the warnings.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.budget_alert_service import (
    SUBJECT_KEY,
    SUBJECT_TEAM,
    SUBJECT_TEAM_MEMBER,
    apply_resets,
    evaluate_region,
    highest_crossed_band,
    mark_notified,
    parse_thresholds,
)
from app.core.config import settings
from app.db.models import (
    DBBudgetAlertState,
    DBPeriodicBudgetLedgerEntry,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBUser,
)
from app.schemas.models import BudgetType

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
# Fixtures
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
    user = DBUser(
        email=email,
        hashed_password="x",
        is_active=True,
        team_id=team.id,
    )
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


def _activity(lite_team_id, spend, *, day_offset=0, hashed_keys=None):
    """Build one day of LiteLLM daily-activity output."""
    day = (datetime.now(UTC) - timedelta(days=day_offset)).date().isoformat()
    api_keys = {
        hashed: {"metrics": {"spend": value}}
        for hashed, value in (hashed_keys or {}).items()
    }
    return {
        "date": day,
        "metrics": {"spend": spend},
        "breakdown": {
            "entities": {lite_team_id: {"metrics": {"spend": spend}}},
            "api_keys": api_keys,
        },
    }


def _patch_litellm(team_rows, user_rows=None, team_keys=None):
    """Patch the three LiteLLM reads the engine can make."""
    return patch.multiple(
        "app.core.budget_alert_service.LiteLLMService",
        get_all_team_daily_activity=AsyncMock(return_value=team_rows),
        get_all_user_daily_activity=AsyncMock(return_value=user_rows or []),
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
    lite_team_id = f"{region.name}_{team.id}"

    # $92 spent of $100 -> 92% -> crosses the 90 band.
    with _patch_litellm([_activity(lite_team_id, 92.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    team_events = [e for e in result.events if e.subject_type == SUBJECT_TEAM]
    assert len(team_events) == 1
    event = team_events[0]
    assert event.threshold_pct == 90
    assert event.max_budget == 100.0
    assert event.spend == 92.0
    assert 91.9 < event.percent_used < 92.1
    assert event.team_id == team.id


@pytest.mark.asyncio
async def test_team_cap_tightens_the_denominator(db, region):
    """An operator team cap lowers the budget, so the same spend crosses higher."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    db.add(
        DBSpendCap(scope="team", region_id=region.id, team_id=team.id, max_budget=50.0)
    )
    db.commit()
    lite_team_id = f"{region.name}_{team.id}"

    # $46 is 46% of $100 but 92% of the $50 cap.
    with _patch_litellm([_activity(lite_team_id, 46.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    assert event.max_budget == 50.0
    assert event.threshold_pct == 90


@pytest.mark.asyncio
async def test_no_budget_means_no_event(db, region):
    """A team with no ledger and no limit has nothing to be a percentage of."""
    team = _make_team(db)
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 500.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_TEAM] == []


@pytest.mark.asyncio
async def test_soft_deleted_team_is_skipped(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    team.deleted_at = datetime.now(UTC)
    db.commit()
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 99.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


@pytest.mark.asyncio
async def test_empty_activity_sweep_produces_nothing(db, region):
    _make_team(db)

    with _patch_litellm([]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []
    assert result.subjects_evaluated == 0


@pytest.mark.asyncio
async def test_unknown_entities_are_ignored(db, region):
    """LiteLLM's own bookkeeping team is not one of ours."""
    _make_team(db)

    with _patch_litellm([_activity("litellm-dashboard", 100.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert result.events == []


# --------------------------------------------------------------------------- #
# Dedup, re-arm, and the POOL moving denominator
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_band_is_not_notified_twice(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"
    rows = [_activity(lite_team_id, 92.0)]

    with _patch_litellm(rows):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    delivered = {e.event_id for e in first.events}
    mark_notified(db, region, first, delivered)

    with _patch_litellm(rows):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in second.events if e.subject_type == SUBJECT_TEAM] == []


@pytest.mark.asyncio
async def test_higher_band_notifies_again_in_the_same_period(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 60.0)]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    assert (
        next(e for e in first.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 50
    )
    mark_notified(db, region, first, {e.event_id for e in first.events})

    with _patch_litellm([_activity(lite_team_id, 91.0)]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert (
        next(e for e in second.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 90
    )


@pytest.mark.asyncio
async def test_topup_lowering_the_percentage_does_not_alert_but_re_arms(db, region):
    """The POOL moving-denominator case.

    Crossing 90%, then buying budget, must not emit anything on the way down --
    but the band has to be rewritten, or the genuine second crossing of 90% is
    suppressed forever.
    """
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    mark_notified(db, region, first, {e.event_id for e in first.events})

    # Top-up: budget becomes $300, so $92 is now ~31%.
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=team.id,
            region_id=region.id,
            entry_type="topup",
            amount_cents=20_000,
            consumed_cents=0,
            is_active=True,
            purchased_at=datetime.now(UTC),
        )
    )
    db.commit()

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    # Nothing announced on the decrease...
    assert [e for e in second.events if e.subject_type == SUBJECT_TEAM] == []
    # ...but the band was walked back.
    apply_resets(db, region, second)
    state = (
        db.query(DBBudgetAlertState)
        .filter(DBBudgetAlertState.subject_key == f"team:{team.id}:{region.id}")
        .one()
    )
    assert state.last_threshold_pct == 0

    # Spending up to 90% of the larger budget alerts again.
    with _patch_litellm([_activity(lite_team_id, 275.0)]):
        third = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert (
        next(e for e in third.events if e.subject_type == SUBJECT_TEAM).threshold_pct
        == 90
    )


@pytest.mark.asyncio
async def test_new_period_re_arms_the_same_band(db, region):
    team = _make_team(db)
    subscription = _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
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

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
        second = await evaluate_region(db, region, thresholds=THRESHOLDS)

    event = next(e for e in second.events if e.subject_type == SUBJECT_TEAM)
    assert event.threshold_pct == 90
    assert event.period_key != old_period_key


@pytest.mark.asyncio
async def test_failed_delivery_leaves_state_unadvanced_and_retries(db, region):
    """No outbox: an undelivered event must be re-detected next tick."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"
    rows = [_activity(lite_team_id, 92.0)]

    with _patch_litellm(rows):
        first = await evaluate_region(db, region, thresholds=THRESHOLDS)
    assert len(first.events) >= 1

    # Delivery failed -> nothing is marked.
    advanced = mark_notified(db, region, first, set())
    assert advanced == 0

    with _patch_litellm(rows):
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
    key = _make_key(db, team, region, token="sk-key-a")
    db.add(DBSpendCap(scope="key", region_id=region.id, key_id=key.id, max_budget=10.0))
    db.commit()

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-a")
    lite_team_id = f"{region.name}_{team.id}"
    rows = [_activity(lite_team_id, 7.6, hashed_keys={hashed: 7.6})]

    with _patch_litellm(rows):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = [e for e in result.events if e.subject_type == SUBJECT_KEY]
    assert len(key_events) == 1
    assert key_events[0].key_id == key.id
    assert key_events[0].max_budget == 10.0
    assert key_events[0].threshold_pct == 75


@pytest.mark.asyncio
async def test_key_without_any_budget_emits_nothing(db, region):
    """POOL keys carry no max_budget unless explicitly capped."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    _make_key(db, team, region, token="sk-key-b")

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-b")
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 900.0, hashed_keys={hashed: 900.0})]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []


@pytest.mark.asyncio
async def test_zero_budget_key_is_skipped(db, region):
    """A pool-gated key is born with max_budget 0; that is not 100% used."""
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    key = _make_key(db, team, region, token="sk-key-c")
    db.add(DBSpendCap(scope="key", region_id=region.id, key_id=key.id, max_budget=0.0))
    db.commit()

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-c")
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 0.0, hashed_keys={hashed: 0.0})]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []


@pytest.mark.asyncio
async def test_expiring_key_is_skipped(db, region):
    """A key being expired (budget_duration 0d) is not a budget warning."""
    team = _make_team(db, budget_type=BudgetType.PERIODIC)
    _add_subscription(db, team, region, amount_cents=100_000)
    _make_key(db, team, region, token="sk-key-d")

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-d")
    lite_team_id = f"{region.name}_{team.id}"
    # PERIODIC teams take the exact-key path, which is where 0d is visible.
    team_keys = [
        {
            "token": hashed,
            "spend": 95.0,
            "max_budget": 100.0,
            "budget_duration": "0d",
        }
    ]

    with _patch_litellm([_activity(lite_team_id, 95.0)], team_keys=team_keys):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_KEY] == []


@pytest.mark.asyncio
async def test_periodic_key_uses_litellm_max_budget(db, region):
    """PERIODIC key budgets live in LiteLLM, and its key spend does reset."""
    team = _make_team(db, budget_type=BudgetType.PERIODIC)
    _add_subscription(db, team, region, amount_cents=100_000)
    key = _make_key(db, team, region, token="sk-key-e")

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-e")
    lite_team_id = f"{region.name}_{team.id}"
    team_keys = [
        {
            "token": hashed,
            "spend": 51.0,
            "max_budget": 100.0,
            "budget_duration": "31d",
        }
    ]

    with _patch_litellm([_activity(lite_team_id, 51.0)], team_keys=team_keys):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    key_events = [e for e in result.events if e.subject_type == SUBJECT_KEY]
    assert len(key_events) == 1
    assert key_events[0].key_id == key.id
    assert key_events[0].max_budget == 100.0
    assert key_events[0].threshold_pct == 50


@pytest.mark.asyncio
async def test_periodic_team_spend_comes_from_key_sum_not_team_counter(db, region):
    """The compounding-counter trap.

    LiteLLM's team entity reports a lifetime figure. For a PERIODIC team the
    engine must use the per-key spends instead, which are what the cycle resets.
    """
    team = _make_team(db, budget_type=BudgetType.PERIODIC)
    _add_subscription(db, team, region, amount_cents=10_000)  # $100 this cycle
    _make_key(db, team, region, token="sk-key-f", name="f")

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-f")
    lite_team_id = f"{region.name}_{team.id}"
    # Team entity claims $4,000 of lifetime spend; the key holds $55 this cycle.
    team_keys = [
        {
            "token": hashed,
            "spend": 55.0,
            "max_budget": 100.0,
            "budget_duration": "31d",
        }
    ]

    with _patch_litellm([_activity(lite_team_id, 4000.0)], team_keys=team_keys):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    team_event = next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    # $55 of $100 -> 50 band, not the 100 band a lifetime total would produce.
    assert team_event.spend == 55.0
    assert team_event.threshold_pct == 50


# --------------------------------------------------------------------------- #
# Team-member scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_team_member_cap_drives_member_alert(db, region):
    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=100_000)
    user = _make_user(db, team)
    _make_key(db, team, region, token="sk-key-g", owner=user)
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

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-g")
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 19.0, hashed_keys={hashed: 19.0})]):
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
    _make_key(db, team, region, token="sk-key-h", owner=user)

    from app.services.litellm import LiteLLMService

    hashed = LiteLLMService.hash_token("sk-key-h")
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 900.0, hashed_keys={hashed: 900.0})]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    assert [e for e in result.events if e.subject_type == SUBJECT_TEAM_MEMBER] == []


# --------------------------------------------------------------------------- #
# Delivery payload
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_webhook_payload_excludes_the_litellm_token(db, region):
    from app.services.budget_alert_webhook import serialize_event

    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
        result = await evaluate_region(db, region, thresholds=THRESHOLDS)

    payload = serialize_event(
        next(e for e in result.events if e.subject_type == SUBJECT_TEAM)
    )
    assert payload["type"] == "budget.threshold_reached"
    assert payload["data"]["threshold_percent"] == 90
    assert payload["data"]["team"]["id"] == team.id
    assert "sk-" not in str(payload)
    assert "token" not in payload["data"]


@pytest.mark.asyncio
async def test_delivery_failure_returns_no_delivered_ids(db, region):
    """A non-2xx must not be reported as delivered, or the alert is lost."""
    from app.services.budget_alert_webhook import deliver_events
    from app.core.budget_alert_service import BudgetAlertEvent

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
async def test_audit_log_records_delivered_and_undelivered_crossings(db, region):
    """budget_alert_state only holds the current band, so the audit log is the
    only durable answer to "was this customer warned"."""
    from app.core.budget_alert_service import write_audit_logs
    from app.db.models import DBAuditLog

    team = _make_team(db)
    _add_subscription(db, team, region, amount_cents=10_000)
    lite_team_id = f"{region.name}_{team.id}"

    with _patch_litellm([_activity(lite_team_id, 92.0)]):
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


@pytest.mark.asyncio
async def test_delivery_without_url_configured_is_a_no_op(db, region):
    from app.services.budget_alert_webhook import deliver_events
    from app.core.budget_alert_service import BudgetAlertEvent

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
