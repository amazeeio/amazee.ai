import pytest

from app.registry.api.sync import create_run, execute_run
from app.registry.models import DBRegistryModelRegion, DBRegistrySyncRun, DBRegistrySyncStep
from tests.registry.api.conftest import US_URL, auth, live_row, messages, model, ok

RUNS = "/registry/sync-runs"


@pytest.fixture
def us(registry_db, writable, live_proxies, managed_proxies):
    managed_proxies.add("local-us1")
    live_proxies.by_url[US_URL] = {
        "model_info": [
            live_row("us-gpt", "gpt-4o", "openai/gpt-4o", input_cost_per_token=2.5e-06),
            live_row("us-claude", "claude-x", "bedrock/us.anthropic.claude-x-v1:0", credential="AWS Bedrock"),
        ],
        "credentials": ["AWS Bedrock", "c1", "c2"],
    }
    return writable


def _entry(**fields):
    return {
        "deployments": [], "deleted": [], "fields": [], "aliases": False, "removed_alias": None,
        "credentials": [], "full": False, "skip_failed": False, **fields,
    }


def _run(db, scope, trigger="write"):
    run = create_run(db, trigger, scope)
    db.commit()
    execute_run(run.id)
    db.expire_all()
    return db.get(DBRegistrySyncRun, run.id)


def _failed_create(registry_db, us, live_proxies):
    """A run where the create of gpt-old fails and the rename of gpt-4o works."""
    new = DBRegistryModelRegion(
        model_id=model(registry_db, "openai", "gpt-old").id, region_id=us["us"].id, model_name="gpt-old",
        litellm_model="openai/gpt-old", model_info={}, litellm_params={},
    )
    registry_db.add(new)
    gpt = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    gpt.model_name = "gpt-renamed"
    registry_db.commit()
    live_proxies.fail[(US_URL, "create_model")] = (500, "boom", False)
    run = _run(registry_db, {str(us["us"].id): _entry(deployments=[gpt.id, new.id])})
    live_proxies.fail.clear()
    live_proxies.writes.clear()
    return run


def test_the_list_filters_by_proxy_and_status(client, admin_token, registry_db, us):
    us_run = create_run(registry_db, "write", {str(us["us"].id): _entry()})
    eu_run = create_run(registry_db, "daily", {str(us["eu"].id): _entry()})
    eu_run.status = "failed"
    registry_db.commit()

    response = client.get(RUNS, headers=auth(admin_token))
    assert [run["id"] for run in ok(response)] == [eu_run.id, us_run.id]
    assert response.headers["X-Total-Count"] == "2"
    [only] = ok(client.get(f"{RUNS}?proxy=local-us1", headers=auth(admin_token)))
    assert (only["id"], only["proxies"], only["trigger"]) == (us_run.id, ["local-us1"], "write")
    assert [run["id"] for run in ok(client.get(f"{RUNS}?status=failed", headers=auth(admin_token)))] == [eu_run.id]


def test_the_detail_shows_the_steps_and_the_result_per_proxy(client, admin_token, registry_db, us, live_proxies):
    run = _failed_create(registry_db, us, live_proxies)

    detail = ok(client.get(f"{RUNS}/{run.id}", headers=auth(admin_token)))

    assert detail["status"] == "failed"
    assert detail["results"] == {"local-us1": "failed"}
    assert [(s["action"], s["status"], s["model_name"]) for s in detail["steps"]] == [
        ("update_model", "ok", "gpt-renamed"), ("create_model", "failed", "gpt-old"),
    ]
    assert client.get(f"{RUNS}/999", headers=auth(admin_token)).status_code == 404


def test_a_retry_sends_only_the_failed_steps(client, admin_token, registry_db, us, live_proxies):
    run = _failed_create(registry_db, us, live_proxies)

    body = ok(client.post(f"{RUNS}/{run.id}/retry", headers=auth(admin_token)), 202)

    assert [w[1] for w in live_proxies.writes] == ["create_model"]
    retry = registry_db.get(DBRegistrySyncRun, body["sync_run_id"])
    registry_db.refresh(retry)
    assert (retry.trigger, retry.status) == ("retry", "ok")
    row = registry_db.query(DBRegistryModelRegion).filter_by(model_name="gpt-old").one()
    registry_db.refresh(row)
    assert (row.litellm_deployment_id, row.sync_error) == ("new-2", None)


def test_a_retry_dry_run_plans_only_the_failed_steps(client, admin_token, registry_db, us, live_proxies):
    run = _failed_create(registry_db, us, live_proxies)

    body = ok(client.post(f"{RUNS}/{run.id}/retry?dry_run=true", headers=auth(admin_token)))

    assert [s["action"] for s in body["steps"]] == ["create_model"]
    assert live_proxies.writes == []
    assert registry_db.query(DBRegistrySyncRun).count() == 1


def test_a_retry_with_no_failed_step_gives_409(client, admin_token, registry_db, us):
    run = _run(registry_db, {str(us["us"].id): _entry()})

    response = client.post(f"{RUNS}/{run.id}/retry", headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["The sync run has no failed step"]


def test_a_repair_puts_back_a_hand_edit(client, admin_token, registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"

    body = ok(client.post("/registry/proxies/local-us1/sync", headers=auth(admin_token)), 202)

    assert live_proxies.writes == [(US_URL, "update_model", "us-gpt", {"model_name": "gpt-4o"})]
    run = registry_db.get(DBRegistrySyncRun, body["sync_run_id"])
    registry_db.refresh(run)
    assert run.trigger == "repair"
    assert run.stats["proxies"]["local-us1"]["drift"] == [
        {"action": "update_model", "model_name": "gpt-4o", "deployment_id": "us-gpt", "fields": ["model_name"]},
    ]
    assert registry_db.query(DBRegistrySyncStep).filter_by(run_id=run.id).count() == 1


def test_a_repair_dry_run_sends_nothing(client, admin_token, registry_db, us, live_proxies):
    live_proxies.by_url[US_URL]["model_info"][0]["model_name"] = "hand-edit"

    body = ok(client.post("/registry/proxies/local-us1/sync?dry_run=true", headers=auth(admin_token)))

    assert [(s["action"], s["changes"]) for s in body["steps"]] == [
        ("update_model", {"model_name": {"from": "hand-edit", "to": "gpt-4o"}}),
    ]
    assert live_proxies.writes == []
    assert registry_db.query(DBRegistrySyncRun).count() == 0


def test_a_repair_of_an_unmanaged_proxy_gives_409(client, admin_token, registry_db, us, live_proxies):
    response = client.post("/registry/proxies/local-eu1/sync", headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["The registry does not manage the proxy"]
    assert live_proxies.writes == []


def test_a_repair_of_a_never_imported_proxy_imports_it_first(client, admin_token, registry_db, us, live_proxies):
    from app.registry.models import DBRegistryProxy

    proxy = registry_db.get(DBRegistryProxy, us["us"].id)
    proxy.imported_at = proxy.adopted_at = None
    registry_db.commit()

    ok(client.post("/registry/proxies/local-us1/sync", headers=auth(admin_token)), 202)

    registry_db.expire_all()
    assert registry_db.get(DBRegistryProxy, us["us"].id).adopted_at is not None


def test_a_retry_adopts_the_proxy_so_the_alias_step_can_pass(client, admin_token, registry_db, us, live_proxies):
    from app.registry.models import DBRegistryAlias, DBRegistryProxy

    registry_db.get(DBRegistryProxy, us["us"].id).adopted_at = None
    registry_db.add(DBRegistryAlias(region_id=us["us"].id, alias="chat", target="gpt-4o"))
    registry_db.commit()
    run = _run(registry_db, {str(us["us"].id): _entry(aliases=True)})
    assert run.status == "failed"

    body = ok(client.post(f"{RUNS}/{run.id}/retry", headers=auth(admin_token)), 202)

    retry = registry_db.get(DBRegistrySyncRun, body["sync_run_id"])
    registry_db.refresh(retry)
    assert retry.status == "ok"
    assert live_proxies.writes == [(US_URL, "set_alias_map", {"chat": "gpt-4o"})]
