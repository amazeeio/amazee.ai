import json
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
    DBRegistryProxy,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistrySyncRun,
    DBRegistrySyncStep,
)
from app.registry.planner import DELETES_EVERY_ALIAS, LIVE_NAME_CLASH, MANAGED_BY, NOT_ADOPTED, NOT_MANAGED
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


def _rename_live(live_proxies, dep_id, name):
    def hook():
        for row in live_proxies.by_url[US_URL]["model_info"]:
            if row["model_info"]["id"] == dep_id:
                row["model_name"] = name

    return hook


@pytest.mark.parametrize("hand_edit, message", [
    ("hand-edit", "the proxy changed after the plan; the next repair plans it again"),
    ("claude-new", "the proxy already has this state"),
])
def test_each_later_step_is_checked_against_the_live_proxy(registry_db, us, live_proxies, hand_edit, message):
    gpt, claude = _row(registry_db, "us-gpt"), _row(registry_db, "us-claude")
    gpt.model_name, claude.model_name = "gpt-renamed", "claude-new"
    registry_db.commit()
    # Someone changes claude-x on the proxy while the gpt-4o step runs.
    live_proxies.hooks["update_model"] = _rename_live(live_proxies, "us-claude", hand_edit)

    run, steps = _run(registry_db, us, deployments=[gpt.id, claude.id])

    assert steps == [("update_model", "ok", None), ("update_model", "skipped", message)]
    assert [w[2] for w in live_proxies.writes] == ["us-gpt"]
    # Only a step that is already done leaves the deployment in sync.
    assert (_row(registry_db, "us-claude").synced_at is not None) is (hand_edit == "claude-new")


def test_a_full_scope_reports_drift_and_unmanaged_deployments_and_deletes_none(registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"].append(live_row("x9", "hand-made", "openai/gpt-4", db_model=None))
    gpt = _row(registry_db, "us-gpt")
    gpt.model_name = "gpt-renamed"
    registry_db.commit()

    run, steps = _run(registry_db, us, full=True)

    proxy = run.stats["proxies"]["local-us1"]
    assert {(d["action"], d["model_name"]) for d in proxy["drift"]} == {
        ("create_credential", None), ("update_model", "gpt-renamed"),
    }
    [update] = [d for d in proxy["drift"] if d["action"] == "update_model"]
    # The full scope also writes the picked price.
    assert update["fields"] == ["model_name", "prices"]
    assert proxy["unmanaged"] == [
        {"model_name": "hand-made", "litellm_model": "openai/gpt-4", "deployment_id": "x9", "db_model": False}
    ]
    assert proxy["failed"] == []
    assert "delete_model" not in _actions(live_proxies)
    assert _row(registry_db, "us-claude").synced_at is not None


def test_a_full_scope_with_skip_failed_leaves_out_and_lists_failed_deployments(registry_db, us, live_proxies):
    claude = _row(registry_db, "us-claude")
    claude.sync_error, claude.model_name = "HTTP 500: boom", "claude-new"
    registry_db.commit()

    run, steps = _run(registry_db, us, full=True, skip_failed=True)

    assert run.stats["proxies"]["local-us1"]["failed"] == ["claude-new"]
    assert all(w[2] != "us-claude" for w in live_proxies.writes if w[1] == "update_model")
    assert _row(registry_db, "us-claude").sync_error == "HTTP 500: boom"


def test_scrub_catches_a_secret_in_escaped_or_multi_line_form():
    from app.registry.api.sync import _scrub, _strings

    private_key = "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0B\nAQEFAASCBKYwggSiAgEAAoIB\n-----END PRIVATE KEY-----\n"
    service_account = json.dumps({"type": "service_account", "private_key": private_key})
    values = {"vertex_credentials": service_account, "nested": {"private_key": private_key}}
    echoes = [json.dumps(values), repr(values), f"key line {private_key.splitlines()[1]} leaked"]

    for echo in echoes:
        scrubbed = _scrub(f"HTTP 400: {echo}", _strings(values))
        assert "MIIEvAIBADANBgkqhkiG9w0B" not in scrubbed, echo
        assert "AQEFAASCBKYwggSiAgEAAoIB" not in scrubbed, echo


def test_no_alias_map_goes_to_a_proxy_that_the_adopt_import_never_read(registry_db, us, live_proxies):
    # A plain import read no aliases, so the registry would delete the live one with its full map.
    registry_db.get(DBRegistryProxy, us.id).adopted_at = None
    registry_db.add(DBRegistryAlias(region_id=us.id, alias="new", target="gpt-4o"))
    registry_db.commit()
    live_proxies.by_url[US_URL]["aliases"] = {"old": "gpt-4o"}

    run, steps = _run(registry_db, us, aliases=True)

    [(action, status, message)] = steps
    assert (action, status) == ("write_alias_map", "failed")
    assert NOT_ADOPTED in message
    assert live_proxies.writes == []


def test_a_disabled_deployment_that_is_already_gone_loses_its_id(registry_db, us, live_proxies):
    claude = _row(registry_db, "us-claude")
    claude.enabled = False
    registry_db.commit()
    live_proxies.by_url[US_URL]["model_info"].pop(1)

    run, steps = _run(registry_db, us, deployments=[claude.id])

    assert steps == []
    assert _row(registry_db, name="claude-x").litellm_deployment_id is None
