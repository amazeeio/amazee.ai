from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.database import engine
from app.registry import config
from app.registry.api.sync import STUCK, run_registry_sync
from app.registry.models import (
    DBRegistryEvent,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryProxy,
    DBRegistryRun,
    DBRegistrySyncRun,
    DBRegistrySyncStep,
)
from app.registry.planner import MANAGED_BY
from tests.registry.api.conftest import EU_URL, US_URL, live_row


@pytest.fixture
def us(registry_db, writable, live_proxies, managed_proxies):
    """local-us1 is registry-managed and in sync with the registry; local-eu1 is not managed."""
    managed_proxies.add("local-us1")
    live_proxies.by_url[US_URL] = {
        "model_info": [
            live_row("us-gpt", "gpt-4o", "openai/gpt-4o", input_cost_per_token=2.5e-06),
            live_row("us-claude", "claude-x", "bedrock/us.anthropic.claude-x-v1:0", credential="AWS Bedrock"),
        ],
        "credentials": ["AWS Bedrock", "c1", "c2"],
    }
    live_proxies.by_url[EU_URL] = {"model_info": [live_row("eu-gpt", "gpt-4o", "openai/gpt-4o")]}
    return writable


def _daily(db):
    stats = run_registry_sync(db)
    db.expire_all()
    return stats


def _sync_state(db):
    db.expire_all()
    return sorted(
        (row.litellm_deployment_id, row.model_name, row.changed_at, row.synced_at, row.sync_error)
        for row in db.query(DBRegistryModelRegion)
    )


def test_the_daily_step_repairs_a_hand_edit_and_records_it(registry_db, us, live_proxies):
    # The first run finds the proxy in sync; then someone renames the deployment by hand.
    _daily(registry_db)
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"

    stats = _daily(registry_db)

    run = registry_db.get(DBRegistrySyncRun, stats["runs"]["local-us1"])
    assert (run.trigger, run.status) == ("daily", "ok")
    assert live_proxies.writes == [(US_URL, "update_model", "us-gpt", {"model_name": "gpt-4o"})]
    [event] = registry_db.query(DBRegistryEvent).all()
    assert (event.type, event.region_id) == ("proxy.drift_repaired", us["us"].id)
    assert event.data == {
        "action": "update_model", "model_name": "gpt-4o", "deployment_id": "us-gpt", "fields": ["model_name"],
        "sync_run_id": run.id,
    }
    gpt = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    assert gpt.changed_at == gpt.synced_at is not None
    assert {run.status for run in registry_db.query(DBRegistryRun).filter_by(step="registry-sync")} == {"ok"}


def test_a_second_daily_run_changes_nothing(registry_db, us, live_proxies):
    _daily(registry_db)
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"
    _daily(registry_db)
    writes, state = list(live_proxies.writes), _sync_state(registry_db)

    _daily(registry_db)

    assert live_proxies.writes == writes
    assert registry_db.query(DBRegistryEvent).count() == 1
    assert _sync_state(registry_db) == state


def test_a_failed_deployment_is_left_out_and_listed(registry_db, us, live_proxies):
    claude = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-claude").one()
    claude.sync_error, claude.model_name = "HTTP 500: boom", "claude-new"
    registry_db.commit()

    stats = _daily(registry_db)

    run = registry_db.get(DBRegistrySyncRun, stats["runs"]["local-us1"])
    assert run.stats["proxies"]["local-us1"]["failed"] == ["claude-new"]
    assert live_proxies.writes == []
    assert registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-claude").one().sync_error


def test_an_unmanaged_live_deployment_is_reported_and_kept(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"].append(live_row("x9", "hand-made", "openai/gpt-4", db_model=None))

    stats = _daily(registry_db)

    run = registry_db.get(DBRegistrySyncRun, stats["runs"]["local-us1"])
    assert [row["deployment_id"] for row in run.stats["proxies"]["local-us1"]["unmanaged"]] == ["x9"]
    assert live_proxies.writes == []


def test_a_stuck_run_is_marked_failed(registry_db, us):
    old = datetime.now(UTC) - timedelta(minutes=config.SYNC_STUCK_MINUTES + 5)
    stuck = DBRegistrySyncRun(trigger="write", status="running", region_ids=[], scope={}, started_at=old)
    fresh = DBRegistrySyncRun(trigger="write", status="pending", region_ids=[], scope={})
    registry_db.add_all([stuck, fresh])
    registry_db.commit()

    stats = _daily(registry_db)

    assert stats["stuck"] == 1
    assert (stuck.status, stuck.error, stuck.finished_at is not None) == ("failed", STUCK, True)
    assert fresh.status == "pending"


def test_an_active_proxy_that_was_never_imported_is_imported(registry_db, us, live_proxies):
    eu_proxy = registry_db.get(DBRegistryProxy, us["eu"].id)
    eu_proxy.imported_at = eu_proxy.adopted_at = None
    registry_db.commit()

    stats = _daily(registry_db)

    assert stats["imported"] == ["local-eu1"]
    assert registry_db.get(DBRegistryProxy, us["eu"].id).imported_at is not None
    # Not registry-managed, so it is imported and gets no write.
    assert {w[0] for w in live_proxies.writes} <= {US_URL}


def test_a_busy_proxy_is_skipped(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"

    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:ns, :id)"), {"ns": config.SYNC_LOCK_NAMESPACE, "id": us["us"].id})
        try:
            stats = _daily(registry_db)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock_all()"))

    run_id = stats["runs"]["local-us1"]
    [step] = registry_db.query(DBRegistrySyncStep).filter_by(run_id=run_id).all()
    assert (step.action, step.status) == ("lock_proxy", "skipped")
    assert registry_db.get(DBRegistrySyncRun, run_id).status == "ok"
    assert live_proxies.writes == []


def test_no_managed_proxy_makes_no_run(registry_db, us, managed_proxies, live_proxies):
    managed_proxies.clear()

    stats = _daily(registry_db)

    assert (stats["managed"], stats["runs"]) == ([], {})
    assert registry_db.query(DBRegistrySyncRun).count() == 0
    assert live_proxies.writes == []


def test_a_proxy_that_only_a_plain_import_read_is_adopted(registry_db, us, live_proxies):
    registry_db.get(DBRegistryProxy, us["eu"].id).adopted_at = None
    registry_db.commit()

    stats = _daily(registry_db)

    assert stats["imported"] == ["local-eu1"]
    assert registry_db.get(DBRegistryProxy, us["eu"].id).adopted_at is not None


def _gpt(db):
    db.expire_all()
    return db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()


def test_the_first_sync_after_an_adopt_import_records_no_drift(registry_db, us, live_proxies):
    # The rows were never in sync, so a difference is the registry's own change, not a hand edit.
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"

    _daily(registry_db)

    assert [w[1] for w in live_proxies.writes] == ["update_model"]
    assert registry_db.query(DBRegistryEvent).count() == 0


def test_a_price_change_on_the_registry_side_records_no_drift(registry_db, us, live_proxies):
    _daily(registry_db)
    gpt_model = registry_db.query(DBRegistryModelPrice).filter_by(model_id=_gpt(registry_db).model_id, scope_kind="base").one()
    gpt_model.prices = {"input_cost_per_token": 3e-06}
    registry_db.commit()

    _daily(registry_db)

    assert live_proxies.writes[-1][3] == {"litellm_params": {"input_cost_per_token": 3e-06}}
    assert registry_db.query(DBRegistryEvent).count() == 0
    # The row keeps the price that is now on the proxy.
    assert _gpt(registry_db).prices == {"input_cost_per_token": 3e-06}


def test_a_hand_edit_of_the_live_price_records_one_drift(registry_db, us, live_proxies):
    _daily(registry_db)
    live_proxies.by_url[US_URL]["model_info"][0]["litellm_params"]["input_cost_per_token"] = 9e-06

    _daily(registry_db)

    [event] = registry_db.query(DBRegistryEvent).all()
    assert event.data["fields"] == ["prices"]


def _registry_made_gpt(db, live_proxies):
    """us-gpt was created by the sync with credential c1, and the proxy runs it with the registry price."""
    gpt = _gpt(db)
    gpt.litellm_params = {"litellm_credential_name": "c1"}
    gpt.model_info = {"id": "us-gpt", "managed_by": MANAGED_BY}
    db.commit()
    live = live_proxies.by_url[US_URL]["model_info"][0]
    live["litellm_params"]["litellm_credential_name"] = "c1"
    live["model_info"]["managed_by"] = MANAGED_BY
    return live


def test_a_replace_after_a_hand_edit_records_one_drift(registry_db, us, live_proxies):
    live = _registry_made_gpt(registry_db, live_proxies)
    _daily(registry_db)
    # Someone adds a cost field by hand that LiteLLM cannot clear.
    live["litellm_params"]["output_cost_per_image"] = 0.2

    _daily(registry_db)

    [event] = registry_db.query(DBRegistryEvent).all()
    assert (event.data["action"], event.data["fields"]) == ("replace_model", ["unclearable_costs"])


def test_a_replace_after_a_registry_price_change_records_no_drift(registry_db, us, live_proxies):
    live = _registry_made_gpt(registry_db, live_proxies)
    _daily(registry_db)
    price = registry_db.query(DBRegistryModelPrice).filter_by(model_id=_gpt(registry_db).model_id, scope_kind="base").one()
    price.prices = {"input_cost_per_token": 3e-06}
    registry_db.commit()
    live["litellm_params"]["output_cost_per_image"] = 0.2

    _daily(registry_db)

    assert "create_model" in [w[1] for w in live_proxies.writes]
    assert registry_db.query(DBRegistryEvent).count() == 0
