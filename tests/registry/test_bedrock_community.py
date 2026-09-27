from datetime import date
from unittest.mock import patch

import pytest

from app.registry.bedrock_community import apply_catalog, parse_catalog, run_bedrock_catalog
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModelLifecycle, DBRegistryRun

CATALOG = [
    {
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        "inferenceTypesSupported": ["INFERENCE_PROFILE"],
        "modelLifecycle": {
            "status": "LEGACY",
            "startOfLifeTime": "2025-05-14 00:00:00+00:00",
            "legacyTime": "2026-04-14 00:00:00+00:00",
            "publicExtendedAccessTime": "2026-07-14 00:00:00+00:00",
            "endOfLifeTime": "2026-10-14 00:00:00+00:00",
        },
        "regions": ["us-east-1", "eu-central-1"],
        "modelCard": {"mantleRegions": ["us-west-2"], "modelEolDate": "October 2026"},
    },
    {
        # Mantle only, no modelArn and no model card.
        "modelId": "anthropic.claude-haiku-4-5",
        "mantleOnly": True,
        "inferenceTypesSupported": ["ON_DEMAND"],
        "modelLifecycle": {"status": "ACTIVE", "startOfLifeTime": "2025-10-01"},
        "regions": ["us-east-1"],
    },
]


def test_parse_catalog_splits_bedrock_and_mantle():
    availability, lifecycle = parse_catalog(CATALOG)

    sonnet = "anthropic.claude-sonnet-4-20250514-v1:0"
    assert availability == {
        ("bedrock", sonnet, "us-east-1"): ["INFERENCE_PROFILE"],
        ("bedrock", sonnet, "eu-central-1"): ["INFERENCE_PROFILE"],
        ("bedrock_mantle", sonnet, "us-west-2"): [],
        ("bedrock_mantle", "anthropic.claude-haiku-4-5", "us-east-1"): [],
    }
    assert lifecycle[("bedrock", sonnet)] == {
        "status": "LEGACY",
        "launched_at": date(2025, 5, 14),
        "legacy_at": date(2026, 4, 14),
        "extended_access_until": date(2026, 7, 14),
        "eol_date": date(2026, 10, 14),
    }
    assert ("bedrock", "anthropic.claude-haiku-4-5") not in lifecycle
    assert lifecycle[("bedrock_mantle", "anthropic.claude-haiku-4-5")]["launched_at"] == date(2025, 10, 1)


@pytest.mark.parametrize("bad", [{"not": "a list"}, [{"regions": []}]])
def test_parse_catalog_rejects_bad_payload(bad):
    with pytest.raises(ValueError):
        parse_catalog(bad)


def test_apply_catalog_is_repeatable_and_drops_regions(registry_db):
    availability, lifecycle = parse_catalog(CATALOG)
    first = apply_catalog(registry_db, availability, lifecycle, date(2026, 9, 27))
    registry_db.commit()
    assert first["changed"] == 3 and first["regions"] == 4

    again = apply_catalog(registry_db, availability, lifecycle, date(2026, 9, 28))
    registry_db.commit()
    assert again["changed"] == 0 and again["regions_dropped"] == 0

    availability.pop(("bedrock", "anthropic.claude-sonnet-4-20250514-v1:0", "eu-central-1"))
    last = apply_catalog(registry_db, availability, lifecycle, date(2026, 9, 29))
    registry_db.commit()
    assert last["regions_dropped"] == 1
    assert registry_db.query(DBRegistryCloudAvailability).count() == 3


def test_run_refuses_a_list_that_lost_most_models(registry_db):
    many = [dict(CATALOG[1], modelId=f"m{i}") for i in range(4)]
    with patch("app.registry.bedrock_community.fetch_model_list", return_value=many):
        run_bedrock_catalog(registry_db, today=date(2026, 9, 27))
    with patch("app.registry.bedrock_community.fetch_model_list", return_value=many[:1]):
        with pytest.raises(RuntimeError, match="refusing"):
            run_bedrock_catalog(registry_db, today=date(2026, 9, 28))

    assert registry_db.query(DBRegistryModelLifecycle).count() == 4
    assert registry_db.query(DBRegistryRun).filter_by(status="failed").count() == 1
