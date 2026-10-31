from datetime import date

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.db.database import engine
from app.registry import config
from app.registry.api.sync import create_run, execute_run
from app.registry.config import CREDENTIAL_KEYS_ENV
from app.registry.crypto import encrypt_values
from app.registry.models import (
    DBRegistryAlias,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistrySyncRun,
    DBRegistrySyncStep,
)
from app.registry.planner import DELETES_EVERY_ALIAS, LIVE_NAME_CLASH, MANAGED_BY, NOT_MANAGED
from tests.registry.api.conftest import US_URL, credential, live_row, model

MASTER_KEY = "sk-master-key-0123"
SECRET = "SECRET-aws-value-0123"


@pytest.fixture
def us(registry_db, writable, live_proxies, managed_proxies, monkeypatch):
    """local-us1 is registry-managed and runs what the registry holds for it."""
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, Fernet.generate_key().decode())
    region = writable["us"]
    region.litellm_api_key = MASTER_KEY
    keys = credential(registry_db, "AWS Keys", provider="bedrock", proxies=[region])
    keys.values_encrypted = encrypt_values({"aws_secret_access_key": SECRET})
    registry_db.commit()
    managed_proxies.add("local-us1")
    live_proxies.by_url[US_URL] = {
        "model_info": [
            live_row("us-gpt", "gpt-4o", "openai/gpt-4o", api_base="https://api.openai.com"),
            live_row("us-claude", "claude-x", "bedrock/us.anthropic.claude-x-v1:0", credential="AWS Bedrock"),
        ],
        "aliases": {},
        "credentials": ["AWS Bedrock", "c1", "c2"],
    }
    return region


def _row(db, dep_id=None, name=None):
    db.expire_all()
    query = db.query(DBRegistryModelRegion)
    return query.filter_by(litellm_deployment_id=dep_id).one() if dep_id else query.filter_by(model_name=name).one()


def _new_row(db, region, name="gpt-old", **fields):
    row = DBRegistryModelRegion(
        model_id=model(db, "openai", "gpt-old").id, region_id=region.id, model_name=name,
        litellm_model="openai/gpt-old", model_info={}, litellm_params=fields.pop("litellm_params", {}), **fields,
    )
    db.add(row)
    db.commit()
    return row


def _entry(**fields):
    return {
        "deployments": [], "deleted": [], "fields": [], "aliases": False, "removed_alias": None,
        "credentials": [], "full": False, "skip_failed": False, **fields,
    }


def _run(db, region, wait=True, **entry):
    run = create_run(db, "write", {str(region.id): _entry(**entry)})
    db.commit()
    execute_run(run.id, wait=wait)
    db.expire_all()
    run = db.get(DBRegistrySyncRun, run.id)
    steps = db.query(DBRegistrySyncStep).filter_by(run_id=run.id).order_by(DBRegistrySyncStep.position).all()
    return run, [(s.action, s.status, s.message) for s in steps]


def _actions(live_proxies):
    return [write[1] for write in live_proxies.writes]


def test_the_run_sends_credentials_then_models_then_the_alias_map(registry_db, us, live_proxies):
    new = _new_row(registry_db, us, litellm_params={})
    gpt, claude = _row(registry_db, "us-gpt"), _row(registry_db, "us-claude")
    gpt.model_name = "gpt-4o-new"
    claude.enabled = False
    registry_db.add(DBRegistryAlias(region_id=us.id, alias="chat", target="gpt-4o-new"))
    registry_db.commit()

    run, steps = _run(
        registry_db, us, deployments=[gpt.id, claude.id, new.id], aliases=True,
        credentials=[{"name": "AWS Keys", "rotate": False, "keep": True}],
    )

    actions = _actions(live_proxies)
    assert actions[0] == "create_credential" and actions[-1] == "set_alias_map"
    assert sorted(actions[1:-1]) == ["create_model", "delete_model", "update_model"]
    assert run.status == "ok" and run.finished_at is not None
    assert all(status == "ok" for _, status, _ in steps)


def test_a_create_stores_the_id_and_sends_the_marker_and_the_price(registry_db, us, live_proxies):
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add(DBRegistryModelPrice(
        model_id=old.id, scope_kind="base", scope="", prices={"input_cost_per_token": 1e-06},
        source="manual", last_seen=date(2026, 1, 1),
    ))
    new = _new_row(registry_db, us)

    run, steps = _run(registry_db, us, deployments=[new.id])

    [(_, action, payload)] = live_proxies.writes
    assert payload["litellm_params"] == {"input_cost_per_token": 1e-06, "model": "openai/gpt-old"}
    assert payload["model_info"]["managed_by"] == MANAGED_BY
    row = _row(registry_db, name="gpt-old")
    assert row.litellm_deployment_id == "new-1"
    assert row.model_info["managed_by"] == MANAGED_BY
    assert row.synced_at is not None and row.sync_error is None
    assert steps == [("create_model", "ok", None)]


def test_an_error_answer_after_a_saved_create_still_stores_the_id(registry_db, us, live_proxies):
    new = _new_row(registry_db, us)
    live_proxies.fail[(US_URL, "create_model")] = (500, "boom", True)

    run, steps = _run(registry_db, us, deployments=[new.id])

    assert _row(registry_db, name="gpt-old").litellm_deployment_id == "new-1"
    assert steps == [("create_model", "ok", "LiteLLM answered HTTP 500, but the deployment exists")]
    assert run.status == "ok"


def test_a_failed_create_stores_a_scrubbed_error(registry_db, us, live_proxies):
    new = _new_row(registry_db, us, litellm_params={"litellm_credential_name": "AWS Keys"})
    live_proxies.by_url[US_URL]["credentials"].append("AWS Keys")
    live_proxies.fail[(US_URL, "create_model")] = (400, f"bad key {SECRET} with {MASTER_KEY}", False)

    run, [(action, status, message)] = _run(registry_db, us, deployments=[new.id])

    assert (action, status, run.status) == ("create_model", "failed", "failed")
    assert message.startswith("HTTP 400: bad key ********")
    assert SECRET not in message and MASTER_KEY not in message
    row = _row(registry_db, name="gpt-old")
    assert row.sync_error == message
    assert row.litellm_deployment_id is None


def test_an_update_sends_only_the_changed_fields(registry_db, us, live_proxies):
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    run, steps = _run(registry_db, us, deployments=[gpt.id])

    assert live_proxies.writes == [(US_URL, "update_model", "us-gpt", {"model_name": "gpt-renamed"})]
    # The live params that the registry does not store stay on the proxy.
    assert live_proxies.by_url[US_URL]["model_info"][0]["litellm_params"]["api_base"] == "https://api.openai.com"
    assert _row(registry_db, "us-gpt").synced_at is not None


def test_a_disable_deletes_the_live_deployment_and_clears_the_id(registry_db, us, live_proxies):
    claude = _row(registry_db, "us-claude")
    claude.enabled = False
    registry_db.commit()

    run, steps = _run(registry_db, us, deployments=[claude.id])

    assert live_proxies.writes == [(US_URL, "delete_model", "us-claude")]
    assert _row(registry_db, name="claude-x").litellm_deployment_id is None
    assert steps == [("delete_model", "ok", None)]


def test_a_deleted_entry_deletes_the_live_deployment(registry_db, us, live_proxies):
    deleted = {"deployment_id": "us-gpt", "model": "openai/gpt-4o", "model_name": "gpt-4o", "litellm_model": "openai/gpt-4o"}

    run, steps = _run(registry_db, us, deleted=[deleted])

    assert live_proxies.writes == [(US_URL, "delete_model", "us-gpt")]
    assert run.status == "ok"


def test_a_config_file_deployment_is_skipped(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"][0]["model_info"]["db_model"] = None
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    run, steps = _run(registry_db, us, deployments=[gpt.id])

    assert steps == [("update_model", "skipped", NOT_MANAGED)]
    assert live_proxies.writes == []
    assert run.status == "ok"


def test_a_live_name_clash_fails_only_its_step(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"].append(live_row("x9", "clash", "openai/gpt-4"))
    new = _new_row(registry_db, us, name="clash")
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    run, steps = _run(registry_db, us, deployments=[gpt.id, new.id])

    [create] = [step for step in steps if step[0] == "create_model"]
    assert create[1] == "failed" and create[2].startswith(LIVE_NAME_CLASH.format("clash"))
    assert ("update_model", "ok", None) in steps
    assert _actions(live_proxies) == ["update_model"]
    assert _row(registry_db, name="clash").sync_error == create[2]
    assert run.status == "failed"


@pytest.mark.parametrize("wait, status, message", [
    (True, "failed", "another sync run holds the proxy"),
    (False, "skipped", "another sync run holds the proxy; the next run repairs it"),
])
def test_a_held_proxy_lock_stops_the_run_for_that_proxy(registry_db, us, live_proxies, monkeypatch, wait, status, message):
    monkeypatch.setattr(config, "SYNC_LOCK_WAIT_SECONDS", 1)
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:ns, :id)"), {"ns": config.SYNC_LOCK_NAMESPACE, "id": us.id})
        try:
            run, steps = _run(registry_db, us, wait=wait, deployments=[gpt.id])
        finally:
            holder.execute(text("SELECT pg_advisory_unlock_all()"))

    assert steps == [("lock_proxy", status, message)]
    assert live_proxies.writes == []
    assert run.status == ("failed" if wait else "ok")


def test_a_proxy_that_the_registry_does_not_manage_gets_nothing(registry_db, us, live_proxies, managed_proxies):
    managed_proxies.clear()
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    run, steps = _run(registry_db, us, deployments=[gpt.id])

    assert steps == [("read_proxy", "failed", "the registry does not manage the proxy")]
    assert live_proxies.writes == [] and live_proxies.calls == []


def test_an_empty_alias_map_never_deletes_live_aliases(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["aliases"] = {"chat": "gpt-4o"}

    run, steps = _run(registry_db, us, aliases=True)

    assert steps == [("write_alias_map", "failed", DELETES_EVERY_ALIAS)]
    assert live_proxies.writes == []


def test_a_row_deleted_during_its_create_has_the_new_deployment_deleted(registry_db, us, live_proxies):
    new = _new_row(registry_db, us)
    new_id = new.id

    def delete_row():
        registry_db.query(DBRegistryModelRegion).filter_by(id=new_id).delete()
        registry_db.commit()

    live_proxies.hooks["create_model"] = delete_row

    run, [(action, status, message)] = _run(registry_db, us, deployments=[new_id])

    assert _actions(live_proxies) == ["create_model", "delete_model"]
    assert live_proxies.writes[1][2] == "new-1"
    assert (action, status) == ("create_model", "ok")
    assert "deleted the new deployment" in message


def test_a_credential_with_no_values_is_never_pushed(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["credentials"] = []

    run, steps = _run(registry_db, us, credentials=[{"name": "c1", "rotate": False, "keep": True}])

    assert steps == [(
        "create_credential", "failed", "the registry has no values for the credential c1, so the sync cannot push it",
    )]
    assert live_proxies.writes == []
