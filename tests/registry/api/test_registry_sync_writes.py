import pytest

from app.registry.models import DBRegistryModelRegion, DBRegistrySyncRun
from tests.registry.api.conftest import EU_URL, US_URL, auth, live_row, ok

PROXIES = "/registry/proxies"


@pytest.fixture
def us(registry_db, writable, live_proxies, managed_proxies):
    """local-us1 is registry-managed and runs what the registry holds; local-eu1 is not managed."""
    managed_proxies.add("local-us1")
    live_proxies.by_url[US_URL] = {
        "model_info": [
            live_row("us-gpt", "gpt-4o", "openai/gpt-4o"),
            live_row("us-claude", "claude-x", "bedrock/us.anthropic.claude-x-v1:0", credential="AWS Bedrock"),
        ],
        "credentials": ["AWS Bedrock", "c1", "c2"],
    }
    live_proxies.by_url[EU_URL] = {"model_info": [live_row("eu-gpt", "gpt-4o", "openai/gpt-4o")]}
    return writable


def _runs(db):
    db.expire_all()
    return db.query(DBRegistrySyncRun).order_by(DBRegistrySyncRun.id).all()


def _row(db, region_id, model_name):
    db.expire_all()
    return db.query(DBRegistryModelRegion).filter_by(region_id=region_id, model_name=model_name).one()


def _put(client, token, proxy, key, body=None, query=""):
    return client.put(f"{PROXIES}/{proxy}/models/{key}{query}", json=body, headers=auth(token))


def test_a_put_on_a_managed_proxy_answers_202_and_creates_the_deployment(client, admin_token, registry_db, us, live_proxies):
    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-old", {"credential": "c1"}), 202)

    [run] = _runs(registry_db)
    assert body["sync_run_id"] == run.id
    assert [d["model_name"] for d in body["result"]] == ["gpt-old"]
    assert (run.trigger, run.status, run.region_ids) == ("write", "ok", [us["us"].id])
    assert [w[1] for w in live_proxies.writes] == ["create_model"]
    row = _row(registry_db, us["us"].id, "gpt-old")
    assert row.litellm_deployment_id == "new-1"
    assert row.changed_at is not None and row.synced_at is not None


def test_a_delete_on_a_managed_proxy_answers_202_and_deletes_the_live_deployment(
    client, admin_token, registry_db, us, live_proxies
):
    response = client.delete(f"{PROXIES}/local-us1/models/openai/gpt-4o", headers=auth(admin_token))

    body = ok(response, 202)
    assert body["result"] is None
    assert live_proxies.writes == [(US_URL, "delete_model", "us-gpt")]
    assert _runs(registry_db)[0].status == "ok"


def test_a_rollout_syncs_only_the_managed_proxy(client, admin_token, registry_db, us, live_proxies):
    body = ok(client.post(
        "/registry/rollouts", json={"models": ["openai/gpt-old"], "proxies": {"local-us1": None, "local-eu1": None}},
        headers=auth(admin_token),
    ), 202)

    [run] = _runs(registry_db)
    assert run.region_ids == [us["us"].id]
    assert len(body["result"]) == 2
    assert {w[0] for w in live_proxies.writes} == {US_URL}


def test_a_price_patch_pushes_the_picked_price(client, admin_token, registry_db, us, live_proxies):
    ok(client.patch(
        "/registry/models/openai/gpt-4o", json={"prices": {"base": {"input_cost_per_token": 3e-06}}},
        headers=auth(admin_token),
    ), 202)

    [(url, action, dep_id, update)] = live_proxies.writes
    assert (url, action, dep_id) == (US_URL, "update_model", "us-gpt")
    assert update == {"litellm_params": {"input_cost_per_token": 3e-06}}


def test_an_unmanaged_proxy_keeps_the_plain_answer(client, admin_token, registry_db, us, live_proxies, managed_proxies):
    managed_proxies.clear()

    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-old", {"credential": "c1"}), 201)

    assert [d["model_name"] for d in body] == ["gpt-old"]
    assert _runs(registry_db) == []
    assert live_proxies.writes == []
    # A write marks the deployment as changed even when no sync runs.
    row = _row(registry_db, us["us"].id, "gpt-old")
    assert row.changed_at is not None and row.synced_at is None


def test_every_model_write_sets_changed_at(client, admin_token, registry_db, us, managed_proxies):
    managed_proxies.clear()
    ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"groups": ["alpha"]}))

    assert _row(registry_db, us["us"].id, "gpt-4o").changed_at is not None
    assert _row(registry_db, us["us"].id, "claude-x").changed_at is None


def test_a_dry_run_sends_nothing_and_saves_no_run(client, admin_token, registry_db, us, live_proxies):
    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-old", {"credential": "c1"}, "?dry_run=true"))

    assert [s["action"] for s in body["steps"]] == ["create_model"]
    assert _runs(registry_db) == []
    assert live_proxies.writes == []
