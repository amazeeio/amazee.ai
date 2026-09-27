from datetime import UTC, date, datetime
from unittest.mock import patch

import httpx

from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryLitellmVersion,
    DBRegistryModel,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
    DBRegistryRun,
)
from app.registry.support import run_support_check

# What the proxy loaded: it can price "priced" (as a geo entry) and nothing else
# of ours. The entry for its "retired" deployment has no price, as LiteLLM adds it.
PROXY_LIST = {
    "us.anthropic.priced-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 1e-06},
    "bedrock/anthropic.retired-v1:0": {"litellm_provider": "bedrock", "mode": "chat"},
    "gpt-4o": {"litellm_provider": "openai", "input_cost_per_token": 1e-06},
}


def _seed(db, region):
    provider = DBRegistryProvider(name="bedrock")
    db.add(provider)
    db.flush()
    ids = {}
    for model_id in ("anthropic.priced-v1:0", "anthropic.retired-v1:0", "anthropic.new-v1:0"):
        row = DBRegistryModel(
            provider_id=provider.id,
            model_id=model_id,
            supports=[],
            status="active",
            source="litellm",
            first_seen=date(2026, 9, 1),
            last_seen=date(2026, 9, 1),
        )
        db.add(row)
        db.flush()
        ids[model_id] = row.id
    for model_id, name in (("anthropic.priced-v1:0", "priced"), ("anthropic.retired-v1:0", "retired")):
        db.add(
            DBRegistryModelRegion(
                model_id=ids[model_id],
                region_id=region.id,
                model_name=name,
                litellm_model=f"bedrock/{model_id}",
                enabled=True,
            )
        )
    db.commit()
    return ids


def test_support_check_compares_proxy_list(registry_db, proxy_region):
    ids = _seed(registry_db, proxy_region)
    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST) as fetch:
        stats = run_support_check(registry_db)

    fetch.assert_called_once_with("http://litellm:4000/public/litellm_model_cost_map")
    assert stats["regions"]["local-us1"] == {
        "version": None,
        "priced": 1,
        "unpriced": 2,
        "deployed_unpriced": ["retired"],
        "deployed_region_unavailable": [],
        "deployed_needs_profile": [],
        "deployed_provisioned_only": [],
    }
    support = {
        row.model_id: row.priced
        for row in registry_db.query(DBRegistryModelSupport).filter_by(region_id=proxy_region.id)
    }
    assert support == {
        ids["anthropic.priced-v1:0"]: True,
        ids["anthropic.retired-v1:0"]: False,
        ids["anthropic.new-v1:0"]: False,
    }

    # After a restart the proxy knows the new model; the same rows are updated.
    restarted = {**PROXY_LIST, "anthropic.new-v1:0": {"litellm_provider": "bedrock", "output_cost_per_token": 0}}
    with patch("app.registry.support.fetch_model_list", return_value=restarted):
        run_support_check(registry_db)
    assert registry_db.query(DBRegistryModelSupport).count() == 3
    new = registry_db.get(DBRegistryModelSupport, (ids["anthropic.new-v1:0"], proxy_region.id))
    assert new.priced is True


def test_unreachable_proxy_keeps_its_last_result(registry_db, proxy_region):
    _seed(registry_db, proxy_region)
    other = DBRegion(name="down", litellm_api_url="http://down:4000", litellm_api_key="k", is_active=True)
    registry_db.add(other)
    registry_db.commit()
    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        run_support_check(registry_db)
    before = {
        (r.model_id, r.priced, r.checked_at)
        for r in registry_db.query(DBRegistryModelSupport).filter_by(region_id=other.id)
    }

    def fetch(url):
        if "down" in url:
            raise httpx.ConnectError("refused")
        return PROXY_LIST

    with patch("app.registry.support.fetch_model_list", side_effect=fetch):
        stats = run_support_check(registry_db)

    assert "down" in stats["unreachable"] and "local-us1" in stats["regions"]
    after = {
        (r.model_id, r.priced, r.checked_at)
        for r in registry_db.query(DBRegistryModelSupport).filter_by(region_id=other.id)
    }
    assert after == before
    assert registry_db.query(DBRegistryRun).filter_by(status="ok").count() == 2


def test_supported_follows_each_region_release(registry_db, proxy_region):
    ids = _seed(registry_db, proxy_region)
    old = DBRegion(name="old", litellm_api_url="http://old:4000", litellm_api_key="k", is_active=True)
    unknown = DBRegion(name="unknown", litellm_api_url="http://unknown:4000", litellm_api_key="k", is_active=True)
    registry_db.add_all([old, unknown])
    registry_db.flush()
    registry_db.add_all(
        [
            DBRegistryProxy(region_id=proxy_region.id, litellm_version="1.105.0"),
            DBRegistryProxy(region_id=old.id, litellm_version="1.102.1"),
            DBRegistryProxy(region_id=unknown.id, litellm_version="1.0.0"),
            DBRegistryLitellmVersion(
                version="1.105.0",
                fetched_at=datetime.now(UTC),
                payload={
                    "anthropic.priced-v1:0": {"litellm_provider": "bedrock"},
                    "anthropic.new-v1:0": {"litellm_provider": "bedrock"},
                },
            ),
            DBRegistryLitellmVersion(
                version="1.102.1",
                fetched_at=datetime.now(UTC),
                payload={"anthropic.priced-v1:0": {"litellm_provider": "bedrock"}},
            ),
            # A release whose list could not be fetched.
            DBRegistryLitellmVersion(version="1.0.0", fetched_at=datetime.now(UTC), error="404"),
        ]
    )
    registry_db.commit()

    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        stats = run_support_check(registry_db)

    def supported(region):
        return {
            row.model_id: row.supported
            for row in registry_db.query(DBRegistryModelSupport).filter_by(region_id=region.id)
        }

    assert supported(proxy_region)[ids["anthropic.new-v1:0"]] is True
    assert supported(old)[ids["anthropic.new-v1:0"]] is False
    assert supported(old)[ids["anthropic.priced-v1:0"]] is True
    assert set(supported(unknown).values()) == {None}
    assert stats["regions"]["local-us1"]["deployed_unsupported"] == ["retired"]
    assert "supported" not in stats["regions"]["unknown"]


def test_region_available_and_needs_profile(registry_db, proxy_region):
    from app.registry.models import DBRegistryCloudAvailability

    ids = _seed(registry_db, proxy_region)
    registry_db.add(DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "us-east-1"}))
    for model_id, region, call_types in (
        ("anthropic.priced-v1:0", "us-east-1", ["INFERENCE_PROFILE"]),
        ("anthropic.retired-v1:0", "us-west-2", ["ON_DEMAND"]),
    ):
        registry_db.add(
            DBRegistryCloudAvailability(
                provider="bedrock", model_id=model_id, cloud_region=region,
                source="bedrock_community", call_types=call_types, last_seen=date(2026, 9, 27),
            )
        )
    registry_db.commit()

    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        stats = run_support_check(registry_db)["regions"]["local-us1"]

    def available(model_id):
        return registry_db.get(DBRegistryModelSupport, (ids[model_id], proxy_region.id)).region_available

    assert available("anthropic.priced-v1:0") is True
    assert available("anthropic.retired-v1:0") is False
    # No source has this model: unknown, not unavailable.
    assert available("anthropic.new-v1:0") is None
    assert stats["deployed_region_unavailable"] == ["retired"]
    # "priced" is deployed with an in-region id but us-east-1 only has a profile.
    assert stats["deployed_needs_profile"] == ["priced"]
    assert stats["deployed_provisioned_only"] == []


def test_provisioned_only_and_mantle_are_not_profile_problems(registry_db, proxy_region):
    from app.registry.models import DBRegistryCloudAvailability, DBRegistryModelRegion

    _seed(registry_db, proxy_region)
    mantle = DBRegistryProvider(name="bedrock_mantle")
    registry_db.add(mantle)
    registry_db.flush()
    opus = DBRegistryModel(
        provider_id=mantle.id, model_id="anthropic.opus", supports=[], status="active",
        source="litellm", first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 1),
    )
    registry_db.add(opus)
    registry_db.flush()
    registry_db.add(
        DBRegistryModelRegion(
            model_id=opus.id, region_id=proxy_region.id, model_name="opus",
            litellm_model="bedrock_mantle/anthropic.opus", enabled=True,
        )
    )
    registry_db.add(
        DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "us-east-1", "bedrock_mantle": "us-east-1"})
    )
    for provider, model_id, call_types in (
        ("bedrock", "anthropic.priced-v1:0", ["PROVISIONED"]),
        ("bedrock_mantle", "anthropic.opus", ["INFERENCE_PROFILE"]),
    ):
        registry_db.add(
            DBRegistryCloudAvailability(
                provider=provider, model_id=model_id, cloud_region="us-east-1",
                source="bedrock_community", call_types=call_types, last_seen=date(2026, 9, 27),
            )
        )
    registry_db.commit()

    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        stats = run_support_check(registry_db)["regions"]["local-us1"]

    assert stats["deployed_provisioned_only"] == ["priced"]
    assert stats["deployed_needs_profile"] == []


def test_region_unknown_to_every_source_is_not_unavailable(registry_db, proxy_region):
    from app.registry.models import DBRegistryCloudAvailability

    ids = _seed(registry_db, proxy_region)
    registry_db.add(DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "eu-central-2"}))
    registry_db.add(
        DBRegistryCloudAvailability(
            provider="bedrock", model_id="anthropic.priced-v1:0", cloud_region="us-east-1",
            source="bedrock_community", call_types=["ON_DEMAND"], last_seen=date(2026, 9, 27),
        )
    )
    registry_db.commit()

    with patch("app.registry.support.fetch_model_list", return_value=PROXY_LIST):
        run_support_check(registry_db)

    row = registry_db.get(DBRegistryModelSupport, (ids["anthropic.priced-v1:0"], proxy_region.id))
    assert row.region_available is None
