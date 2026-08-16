"""Budget-cycle semantics against a real LiteLLM proxy.

app/core/spend_period_service.py is reverse-engineered from how LiteLLM
computes budget_reset_at: rolling windows for "Nd" (31d exists precisely to
get day-it-was-set cycles), a snap to the 1st of the next calendar month for
"1mo"/"30d". These tests pin the proxy's actual behavior to that math — the
exact drift a LiteLLM bump would introduce.

The forced-reset test is the only place the suite touches LiteLLM's own
Postgres (backdating budget_reset_at so the reset job fires within the
test-config rescheduler interval). If a bump renames those columns, this
breaks loudly — that is working as intended for a version-drift detector.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from app.core.spend_period_service import compute_period_start
from app.services.litellm import LiteLLMService, hash_litellm_token
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    completion,
    wait_for_key_spend,
)

LITELLM_A_DB_URL = "postgresql://llmproxy:dbpassword9090@litellm_db:5432/litellm"


def _parse_dt(value):
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _key_budget_state(token) -> dict:
    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    info = await service.get_key_info(token)
    return info.get("info", info)


async def _wait_for_key_state(token, predicate, timeout=60, message="key state"):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = await _key_budget_state(token)
        if predicate(state):
            return state
        await asyncio.sleep(1)
    raise AssertionError(
        f"timed out after {timeout}s waiting for {message}; last state: "
        f"spend={state.get('spend')}, "
        f"budget_reset_at={state.get('budget_reset_at')}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", ["31d", "7d"])
async def test_rolling_duration_gives_rolling_window(
    litellm_region, make_team, make_key, duration
):
    """"Nd" windows must contain now and be exactly N days long.

    Found on v1.95.0: LiteLLM snaps "7d" to calendar-week boundaries (reset
    on Monday midnight), while "31d" stays anchored on when the cap was set.
    Both satisfy the invariant the backend's period math actually relies on:
    compute_period_start(reset_at, Nd) <= now < reset_at, window length == Nd.
    A bump that breaks either property breaks spend-period attribution.
    """
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    await service.update_key_budget(
        token, budget_duration=duration, max_budget=100.0
    )

    state = await _key_budget_state(token)
    assert state.get("budget_duration") == duration
    reset_at = _parse_dt(state.get("budget_reset_at"))
    assert reset_at is not None, f"no budget_reset_at for {duration}: {state}"

    now = datetime.now(UTC)
    days = int(duration.removesuffix("d"))
    period_start = compute_period_start(reset_at, duration)

    assert reset_at - period_start == timedelta(days=days), (
        f"{duration}: window derived from budget_reset_at={reset_at} is not "
        f"exactly {days} days — length assumption broken on this version"
    )
    # A day's slack on each side: proxies snap resets to midnight/boundaries.
    assert (
        period_start - timedelta(hours=26)
        <= now
        <= reset_at + timedelta(hours=26)
    ), (
        f"{duration}: current moment {now} outside the derived window "
        f"[{period_start}, {reset_at}] — the backend would attribute spend "
        "to the wrong period on this LiteLLM version"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", ["1mo", "30d"])
async def test_monthly_durations_snap_to_first_of_next_month(
    litellm_region, make_team, make_key, duration
):
    """spend_period_service assumes "1mo"/"30d" reset on the 1st of the next
    calendar month (LiteLLM's documented-by-behavior special case)."""
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    await service.update_key_budget(
        token, budget_duration=duration, max_budget=100.0
    )

    state = await _key_budget_state(token)
    reset_at = _parse_dt(state.get("budget_reset_at"))
    assert reset_at is not None, f"no budget_reset_at for {duration}: {state}"

    now = datetime.now(UTC)
    first_of_next_month = (now.replace(day=1) + timedelta(days=32)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    assert (reset_at.year, reset_at.month, reset_at.day) == (
        first_of_next_month.year,
        first_of_next_month.month,
        1,
    ), (
        f"{duration}: proxy set budget_reset_at={reset_at}, backend assumes "
        f"the 1st-of-next-month snap ({first_of_next_month.date()}) — "
        "monthly special-case broken on this LiteLLM version"
    )

    period_start = compute_period_start(reset_at, duration)
    assert period_start == now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


@pytest.mark.asyncio
async def test_forced_reset_zeroes_spend_and_rolls_window(
    litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    await service.update_key_budget(
        token, budget_duration="31d", max_budget=100.0
    )
    assert completion(LITELLM_A_URL, token).status_code == 200
    await wait_for_key_spend(LITELLM_A_URL, token)

    # Backdate the reset so the proxy's own scheduler (3-6s in the test
    # config) treats the window as elapsed and performs a real reset.
    engine = create_engine(LITELLM_A_DB_URL)
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    'UPDATE "LiteLLM_VerificationToken" '
                    "SET budget_reset_at = :past WHERE token = :tok"
                ),
                {
                    "past": datetime.now(UTC) - timedelta(hours=1),
                    "tok": hash_litellm_token(token),
                },
            )
            conn.commit()
            assert result.rowcount == 1, (
                "key row not found in LiteLLM_VerificationToken — "
                "schema drift on this LiteLLM version?"
            )
    finally:
        engine.dispose()

    before_reset = datetime.now(UTC)
    state = await _wait_for_key_state(
        token,
        lambda s: (s.get("spend") or 0) == 0,
        message="proxy reset job to zero the key's spend",
    )

    new_reset_at = _parse_dt(state.get("budget_reset_at"))
    assert new_reset_at is not None and new_reset_at > before_reset, (
        f"reset fired but budget_reset_at did not roll forward: {new_reset_at}"
    )
    # The rolled window must still be what the backend predicts for 31d:
    # a rolling month from the reset, not a calendar snap.
    period_start = compute_period_start(new_reset_at, "31d")
    drift = abs((period_start - before_reset).total_seconds())
    assert drift < 26 * 3600, (
        f"post-reset window start {period_start} is {drift / 3600:.1f}h from "
        "the reset moment — 31d rolled to something other than a rolling month"
    )

    # And the key must work again.
    assert completion(LITELLM_A_URL, token).status_code == 200
