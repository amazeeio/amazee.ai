from datetime import UTC, datetime
from unittest.mock import patch

import httpx

from app.db.models import DBRegion
from app.registry.models import DBRegistryLitellmVersion, DBRegistryProxy
from app.registry.versions import fetch_release_lists, run_version_check

RELEASE = {"anthropic.x-v1:0": {"litellm_provider": "bedrock"}}


def test_release_list_is_fetched_once(registry_db):
    with patch("app.registry.versions.fetch_model_list", return_value=RELEASE) as fetch:
        first = fetch_release_lists(registry_db, {"1.102.1"}, datetime.now(UTC))
        second = fetch_release_lists(registry_db, {"1.102.1"}, datetime.now(UTC))

    assert fetch.call_count == 1
    assert "/v1.102.1/" in fetch.call_args.args[0]
    assert first["fetched"] == ["1.102.1"] and second["fetched"] == []
    row = registry_db.get(DBRegistryLitellmVersion, "1.102.1")
    assert row.payload == RELEASE and row.model_count == 1 and row.error is None


def test_missing_release_is_stored_with_error_and_retried(registry_db):
    missing = httpx.HTTPStatusError("404", request=httpx.Request("GET", "u"), response=httpx.Response(404))
    with patch("app.registry.versions.fetch_model_list", side_effect=missing):
        stats = fetch_release_lists(registry_db, {"1.103.0"}, datetime.now(UTC))
    assert "1.103.0" in stats["failed"]
    assert registry_db.get(DBRegistryLitellmVersion, "1.103.0").payload is None

    with patch("app.registry.versions.fetch_model_list", return_value=RELEASE) as fetch:
        stats = fetch_release_lists(registry_db, {"1.103.0"}, datetime.now(UTC))
    assert fetch.call_count == 1 and stats["fetched"] == ["1.103.0"]
    row = registry_db.get(DBRegistryLitellmVersion, "1.103.0")
    assert row.payload == RELEASE and row.error is None


def test_non_release_version_is_never_fetched(registry_db):
    with patch("app.registry.versions.fetch_model_list") as fetch:
        stats = fetch_release_lists(registry_db, {"1.102.1-nightly", "../main"}, datetime.now(UTC))
    fetch.assert_not_called()
    assert set(stats["failed"]) == {"1.102.1-nightly", "../main"}


def test_version_check_updates_proxies_and_keeps_unknown(registry_db, proxy_region):
    hidden = DBRegion(name="hidden", litellm_api_url="http://hidden:4000", litellm_api_key="k", is_active=True)
    registry_db.add(hidden)
    registry_db.flush()
    registry_db.add(DBRegistryProxy(region_id=hidden.id, litellm_version="1.101.0"))
    registry_db.commit()

    def version(self):
        return None if "hidden" in str(self._client.base_url) else "1.105.0"

    with (
        patch("app.registry.versions.ProxyClient.version", version),
        patch("app.registry.versions.fetch_model_list", return_value=RELEASE),
    ):
        stats = run_version_check(registry_db)

    assert stats["versions"] == {"local-us1": "1.105.0", "hidden": "1.101.0"}
    assert stats["unknown"] == ["hidden"]
    assert registry_db.get(DBRegistryProxy, proxy_region.id).litellm_version == "1.105.0"
    assert registry_db.get(DBRegistryProxy, hidden.id).litellm_version == "1.101.0"
    assert sorted(stats["fetched"]) == ["1.101.0", "1.105.0"]


def test_one_bad_region_does_not_stop_the_others(registry_db, proxy_region):
    bad = DBRegion(name="bad", litellm_api_url="http://bad-host:4000", litellm_api_key="k", is_active=True)
    registry_db.add(bad)
    registry_db.commit()

    def version(self):
        if "bad-host" in str(self._client.base_url):
            raise AttributeError("'list' object has no attribute 'get'")
        return "1.105.0"

    with (
        patch("app.registry.versions.ProxyClient.version", version),
        patch("app.registry.versions.fetch_model_list", return_value=RELEASE),
    ):
        stats = run_version_check(registry_db)

    assert stats["versions"] == {"local-us1": "1.105.0"} and stats["unknown"] == ["bad"]
