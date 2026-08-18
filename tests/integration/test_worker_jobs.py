"""Worker jobs invoked directly as functions against real LiteLLM proxies.

Covered: apply_billing_cycle_for_team (the hardcoded 31d + Stripe-30d safety
net), monitor_teams (smoke — SESService construction fails gracefully without
AWS creds), hard_delete_expired_teams (cascades into LiteLLM key deletion and
the region's Postgres).
Trial jobs (monitor_trial_users / reap_trial_keys) are follow-up: their setup
needs the public trial flow, see litellm-integration-tests-plan.md.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.worker import (
    apply_billing_cycle_for_team,
    hard_delete_expired_teams,
    monitor_teams,
)
from app.db.models import DBTeam
from app.services.litellm import LiteLLMService
from tests.conftest import soft_delete_team_for_test
from tests.integration.conftest import (
    LITELLM_A_URL,
    LITELLM_MASTER_KEY,
    completion,
    wait_for,
)


@pytest.mark.asyncio
async def test_apply_billing_cycle_sets_litellm_team_budget(
    db, litellm_region, make_team, make_key
):
    team = make_team()  # default budget_type "periodic" supports cycles
    key = make_key(team_id=team["id"], region_id=litellm_region.id)

    now = datetime.now(UTC)
    sync_errors = await apply_billing_cycle_for_team(
        db,
        team_id=team["id"],
        budget_cents=5000,
        region_id=litellm_region.id,
        period_start=now,
        period_end=now + timedelta(days=30),
    )
    assert sync_errors == [], f"billing cycle sync errors: {sync_errors}"

    service = LiteLLMService(LITELLM_A_URL, LITELLM_MASTER_KEY)
    lt_team_id = LiteLLMService.format_team_id(litellm_region.name, team["id"])
    info = await service.get_team_info(lt_team_id)
    team_info = info.get("team_info", info)
    assert float(team_info.get("max_budget") or 0) == 50.0
    # The 31d (not 30d) duration is the deliberate Stripe safety net.
    assert team_info.get("budget_duration") == "31d"

    # Keys must still work after the cycle applies.
    assert completion(LITELLM_A_URL, key["litellm_token"]).status_code == 200


@pytest.mark.asyncio
async def test_monitor_teams_runs_clean_against_real_proxy(
    db, litellm_region, make_team, make_key
):
    """Smoke: the daily monitor walks teams/keys against the real proxy
    without raising (spend lookups, budget checks, metrics)."""
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    assert completion(LITELLM_A_URL, key["litellm_token"]).status_code == 200

    await monitor_teams(db)


@pytest.mark.asyncio
async def test_hard_delete_cascades_to_litellm_and_region_db(
    db, litellm_region, make_team, make_key
):
    team = make_team()
    key = make_key(team_id=team["id"], region_id=litellm_region.id)
    token = key["litellm_token"]
    assert completion(LITELLM_A_URL, token).status_code == 200

    db_team = db.query(DBTeam).filter(DBTeam.id == team["id"]).first()
    soft_delete_team_for_test(
        db, db_team, deleted_at=datetime.now(UTC) - timedelta(days=365)
    )

    await hard_delete_expired_teams(db)

    assert db.query(DBTeam).filter(DBTeam.id == team["id"]).first() is None

    def key_rejected():
        resp = completion(LITELLM_A_URL, token)
        return resp if resp.status_code >= 400 else None

    wait_for(key_rejected, message="hard-deleted team's key to be rejected")
