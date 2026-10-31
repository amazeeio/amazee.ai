import json

import pytest
from cryptography.fernet import Fernet

from app.registry.config import CREDENTIAL_KEYS_ENV
from app.registry.models import DBRegistryModelRegion, DBRegistryProxy, DBRegistrySyncRun, DBRegistrySyncStep
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


SECRET = "SECRET-api-key-0123"


def test_a_group_put_updates_the_live_access_groups(client, admin_token, us, live_proxies):
    body = {"models": [{"proxy": "local-us1", "model": "openai/gpt-4o"}]}
    ok(client.put("/registry/access-groups/alpha/models", json=body, headers=auth(admin_token)), 202)

    assert live_proxies.writes == [(US_URL, "update_model", "us-gpt", {"model_info": {"access_groups": ["alpha"]}})]


def test_an_alias_put_sends_the_full_map(client, admin_token, us, live_proxies):
    ok(client.put("/registry/aliases/local-us1/chat", json={"target": "gpt-4o"}, headers=auth(admin_token)), 202)

    assert live_proxies.writes == [(US_URL, "set_alias_map", {"chat": "gpt-4o"})]


@pytest.fixture
def credential_key(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, Fernet.generate_key().decode())


def _create(client, token):
    return client.post(
        "/registry/credentials",
        json={"name": "Open AI", "provider": "openai", "values": {"api_key": SECRET}, "proxies": ["local-us1"]},
        headers=auth(token),
    )


def test_credential_writes_push_the_values_only_to_the_proxy(
    client, admin_token, registry_db, us, live_proxies, credential_key
):
    created = _create(client, admin_token)
    patched = client.patch(
        "/registry/credentials/Open AI", json={"values": {"api_key": SECRET + "-new"}}, headers=auth(admin_token)
    )
    deleted = client.delete("/registry/credentials/Open AI", headers=auth(admin_token))

    assert [ok(r, 202)["sync_run_id"] for r in (created, patched, deleted)] == [1, 2, 3]
    info = {"custom_llm_provider": "openai"}
    assert [w[1:] for w in live_proxies.writes] == [
        ("create_credential", "Open AI", {"api_key": SECRET}, info),
        ("update_credential", "Open AI", {"api_key": SECRET + "-new"}, info),
        ("delete_credential", "Open AI"),
    ]
    assert "Open AI" not in live_proxies.by_url[US_URL]["credentials"]
    stored = [
        (step.payload, step.changes, step.message)
        for step in registry_db.query(DBRegistrySyncStep)
    ] + [(run.scope, run.stats, run.error) for run in _runs(registry_db)]
    assert SECRET not in json.dumps(stored) + created.text + patched.text + deleted.text


def test_credential_writes_on_an_unmanaged_proxy_keep_their_status(
    client, admin_token, us, live_proxies, managed_proxies, credential_key
):
    managed_proxies.clear()

    ok(_create(client, admin_token), 201)
    assert client.delete("/registry/credentials/Open AI", headers=auth(admin_token)).status_code == 204
    assert live_proxies.writes == []


def test_clearing_the_credential_of_a_live_deployment_on_a_managed_proxy_is_refused(
    client, admin_token, registry_db, us, live_proxies
):
    before = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-claude").one().litellm_params

    response = _put(client, admin_token, "local-us1", "bedrock/anthropic.claude-x-v1:0", {"credential": None})

    assert response.status_code == 409
    assert response.json()["problems"][0]["message"].startswith("LiteLLM keeps a credential name")
    assert _row(registry_db, us["us"].id, "claude-x").litellm_params == before
    assert _runs(registry_db) == [] and live_proxies.writes == []


@pytest.mark.parametrize("managed", [True, False])
def test_the_write_body_shows_the_saved_changed_at(client, admin_token, registry_db, us, managed_proxies, managed):
    if not managed:
        managed_proxies.clear()

    body = ok(_put(client, admin_token, "local-us1", "openai/gpt-4o", {"groups": ["alpha"]}), 202 if managed else 200)
    detail = ok(client.patch(
        "/registry/models/openai/gpt-4o", json={"prices": {"base": {"input_cost_per_token": 3e-06}}},
        headers=auth(admin_token),
    ), 202 if managed else 200)

    [deployment] = body["result"] if managed else body
    assert deployment["changed_at"] is not None
    model = detail["result"] if managed else detail
    assert all(d["changed_at"] for d in model["deployments"] if d["proxy"] == "local-us1")


def _forget_adoption(db, us, live_proxies):
    db.get(DBRegistryProxy, us["us"].id).adopted_at = None
    db.commit()
    live_proxies.by_url[US_URL]["aliases"] = {"old": "gpt-4o"}


def test_an_alias_write_on_a_proxy_that_was_never_adopted_adopts_it_first(
    client, admin_token, registry_db, us, live_proxies
):
    _forget_adoption(registry_db, us, live_proxies)

    body = ok(client.put("/registry/aliases/local-us1/chat", json={"target": "gpt-4o"}, headers=auth(admin_token)), 202)

    assert live_proxies.writes == [(US_URL, "set_alias_map", {"old": "gpt-4o", "chat": "gpt-4o"})]
    steps = registry_db.query(DBRegistrySyncStep).filter_by(run_id=body["sync_run_id"]).all()
    assert [(s.action, s.status) for s in steps] == [("write_alias_map", "ok")]


def test_an_alias_delete_can_remove_a_live_alias_that_the_adopt_import_reads(
    client, admin_token, registry_db, us, live_proxies
):
    _forget_adoption(registry_db, us, live_proxies)

    ok(client.delete("/registry/aliases/local-us1/old", headers=auth(admin_token)), 202)

    assert live_proxies.writes == [(US_URL, "set_alias_map", {})]


def test_an_alias_dry_run_imports_nothing(client, admin_token, registry_db, us, live_proxies):
    _forget_adoption(registry_db, us, live_proxies)

    ok(client.put("/registry/aliases/local-us1/chat?dry_run=true", json={"target": "gpt-4o"}, headers=auth(admin_token)))

    registry_db.expire_all()
    assert registry_db.get(DBRegistryProxy, us["us"].id).adopted_at is None


def test_a_pushed_credential_names_its_provider_and_region_in_its_info(
    client, admin_token, registry_db, us, live_proxies, credential_key
):
    body = {
        "name": "AWS Keys", "provider": "bedrock", "proxies": ["local-us1"], "info": {"note": "main"},
        "values": {"aws_access_key_id": SECRET, "aws_region_name": "us-east-1", "vertex_location": "us-central1"},
    }

    ok(client.post("/registry/credentials", json=body, headers=auth(admin_token)), 202)

    # A later import reads the cloud region from the info, and LiteLLM the provider.
    [(_, _, _, _, info)] = live_proxies.writes
    assert info == {
        "note": "main", "custom_llm_provider": "bedrock", "aws_region_name": "us-east-1", "vertex_location": "us-central1",
    }


def test_a_retry_does_not_delete_a_credential_that_was_added_back(
    client, admin_token, registry_db, us, live_proxies, credential_key
):
    ok(_create(client, admin_token), 202)
    live_proxies.fail[(US_URL, "delete_credential")] = (500, "boom", False)
    removed = ok(client.patch("/registry/credentials/Open AI", json={"proxies": []}, headers=auth(admin_token)), 202)
    live_proxies.fail.clear()
    ok(client.patch("/registry/credentials/Open AI", json={"proxies": ["local-us1"]}, headers=auth(admin_token)), 202)

    ok(client.post(f"/registry/sync-runs/{removed['sync_run_id']}/retry", headers=auth(admin_token)), 202)

    # The proxy is linked again, so the retry keeps the credential.
    assert "Open AI" in live_proxies.by_url[US_URL]["credentials"]
    assert [w for w in live_proxies.writes if w[1] == "delete_credential"] == [(US_URL, "delete_credential", "Open AI")]
