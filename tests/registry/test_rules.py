from datetime import date

import pytest

from app.registry.config import BEDROCK_GEO_PREFIXES
from app.registry.models import DBRegistryCloudAvailability
from app.registry.rules import _GEOS_BY_FAMILY, _GEOS_BY_REGION, backend_problem, default_backend, geo_prefixes
from app.registry.support import Availability

CLAUDE = "anthropic.claude-x-v1:0"
US = {"bedrock": "us-east-1"}


@pytest.mark.parametrize(
    "region, expected",
    [
        ("us-gov-west-1", ("us-gov",)),
        ("us-east-1", ("us",)),
        ("eu-central-1", ("eu",)),
        ("ap-southeast-2", ("au", "apac")),
        ("ap-southeast-4", ("au", "apac")),
        ("ap-northeast-1", ("jp", "apac")),
        ("ap-south-1", ("apac",)),
        ("ca-central-1", ("ca",)),
        ("sa-east-1", ("sa",)),
        ("il-central-1", ("il",)),
        ("mx-central-1", ("mx",)),
        (None, ()),
        ("", ()),
        ("me-central-1", ()),
    ],
)
def test_geo_prefixes(region, expected):
    assert geo_prefixes(region) == expected


def test_every_geo_prefix_is_a_bedrock_geo_prefix():
    geos = {g for _, gs in _GEOS_BY_FAMILY for g in gs} | {g for gs in _GEOS_BY_REGION.values() for g in gs}
    assert geos <= BEDROCK_GEO_PREFIXES
    assert "global" not in geos


@pytest.mark.parametrize(
    "backend, provider, model_id, cloud_regions",
    [
        (f"bedrock/us.{CLAUDE}", "bedrock", CLAUDE, US),
        (f"bedrock/{CLAUDE}", "bedrock", CLAUDE, US),
        (f"bedrock_converse/us.{CLAUDE}", "bedrock", CLAUDE, US),
        ("azure/eu/gpt-4o", "azure", "gpt-4o", {}),
        ("openai/gpt-4o", "openai", "gpt-4o", {}),
    ],
)
def test_backend_problem_passes(backend, provider, model_id, cloud_regions):
    assert backend_problem(backend, provider, model_id, cloud_regions) is None


@pytest.mark.parametrize(
    "backend, provider, model_id, cloud_regions, problem",
    [
        (f"bedrock/eu.{CLAUDE}", "bedrock", CLAUDE, US, "the geo prefix eu is not allowed on this proxy"),
        (f"bedrock/global.{CLAUDE}", "bedrock", CLAUDE, US, "the geo prefix global is not allowed on this proxy"),
        (f"bedrock/us.{CLAUDE}", "bedrock", CLAUDE, {}, "the geo prefix us is not allowed on this proxy"),
        ("bedrock/us.anthropic.other-v1:0", "bedrock", CLAUDE, US, "the backend is for another model"),
        (f"openai/{CLAUDE}", "bedrock", CLAUDE, US, "the backend is for another model"),
        (CLAUDE, "bedrock", CLAUDE, US, "the backend is for another model"),
        (
            f"bedrock/us-east-1/{CLAUDE}", "bedrock", CLAUDE, US,
            "the backend is not a model string the registry deploys",
        ),
    ],
)
def test_backend_problem_refuses(backend, provider, model_id, cloud_regions, problem):
    assert backend_problem(backend, provider, model_id, cloud_regions) == problem


def _availability(db, call_types):
    db.add(
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=CLAUDE, cloud_region="us-east-1",
            source="bedrock_community", call_types=call_types, last_seen=date(2026, 9, 27),
        )
    )
    db.flush()
    return Availability(db)


def test_default_backend_is_the_geo_profile_when_the_region_offers_one(registry_db):
    availability = _availability(registry_db, ["ON_DEMAND", "INFERENCE_PROFILE"])
    assert default_backend("bedrock", CLAUDE, US, availability) == f"bedrock/us.{CLAUDE}"


def test_default_backend_is_the_in_region_id_on_demand(registry_db):
    availability = _availability(registry_db, ["ON_DEMAND"])
    assert default_backend("bedrock", CLAUDE, US, availability) == f"bedrock/{CLAUDE}"


def test_default_backend_with_no_cloud_region_is_the_in_region_id(registry_db):
    availability = _availability(registry_db, ["INFERENCE_PROFILE"])
    assert default_backend("bedrock", CLAUDE, {}, availability) == f"bedrock/{CLAUDE}"


def test_default_backend_of_another_provider(registry_db):
    assert default_backend("openai", "gpt-4o", US, Availability(registry_db)) == "openai/gpt-4o"
