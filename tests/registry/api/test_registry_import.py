import httpx

from app.registry.models import DBRegistryAlias, DBRegistryCredential, DBRegistryModelRegion, DBRegistryRun
from tests.registry.api.conftest import US_URL, auth, live_row, messages, ok
from tests.registry.api.conftest import deployment_state as _state

IMPORT = "/registry/proxies/local-us1/import"


def _live(live_proxies):
    live_proxies.by_url[US_URL] = {
        "model_info": [
            live_row("us-gpt", "gpt-4o", "openai/gpt-4o"),
            live_row("us-new", "gpt-old", "openai/gpt-old"),
        ],
        "aliases": {"chat": "gpt-4o"},
        "credentials": ["AWS Bedrock", "fresh"],
    }


def test_the_import_endpoint_adopts_a_new_live_deployment(client, admin_token, registry_db, writable, live_proxies):
    _live(live_proxies)

    stats = ok(client.post(IMPORT, headers=auth(admin_token)))

    assert (stats["imported"], stats["kept"], stats["aliases_added"], stats["credentials_added"]) == (1, 1, 1, 1)
    registry_db.expire_all()
    assert registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-new").count() == 1
    assert [(a.alias, a.target) for a in registry_db.query(DBRegistryAlias)] == [("chat", "gpt-4o")]
    fresh = registry_db.query(DBRegistryCredential).filter_by(name="fresh").one()
    assert (fresh.provider, fresh.values_encrypted) == (None, None)
    assert registry_db.query(DBRegistryRun).filter_by(step="proxy-import").one().status == "ok"


def test_the_import_dry_run_saves_nothing(client, admin_token, registry_db, writable, live_proxies):
    _live(live_proxies)
    before = _state(registry_db)

    body = ok(client.post(f"{IMPORT}?dry_run=true", headers=auth(admin_token)))

    assert (body["result"]["imported"], body["steps"]) == (1, [])
    assert _state(registry_db) == before
    assert registry_db.query(DBRegistryAlias).count() == 0
    assert registry_db.query(DBRegistryRun).count() == 0


def test_an_import_of_a_proxy_that_cannot_be_read_gives_409(client, admin_token, registry_db, writable, live_proxies):
    live_proxies.by_url[US_URL] = httpx.ConnectError("down")
    before = _state(registry_db)

    response = client.post(IMPORT, headers=auth(admin_token))

    assert response.status_code == 409
    assert messages(response) == ["The automatic import of local-us1 failed: ConnectError"]
    assert _state(registry_db) == before
