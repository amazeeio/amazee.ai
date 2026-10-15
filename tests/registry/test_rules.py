from datetime import UTC, date, datetime

import pytest

from app.db.models import DBRegion
from app.registry.config import BEDROCK_GEO_PREFIXES
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModel, DBRegistryModelSupport
from app.registry.rules import (
    _GEOS_BY_FAMILY,
    _GEOS_BY_REGION,
    backend_problem,
    default_backend,
    enable_problems,
    geo_prefixes,
    removal_problem,
    support_rows,
)
from app.registry.support import Availability
from tests.registry.conftest import add_model

CLAUDE = "anthropic.claude-x-v1:0"
US = {"bedrock": "us-east-1"}
CHECKED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TODAY = date(2026, 6, 1)


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


UNKNOWN = "support is unknown: the support check has no result for this proxy"
UNSUPPORTED = "the proxy's LiteLLM version does not support the model"
UNAVAILABLE = "the proxy's cloud region does not offer the model"
PROVISIONED = "the proxy's cloud region offers the model only with provisioned throughput"
BLOCKED = "the model is blocked"


def _support(supported=True, region_available=True):
    return DBRegistryModelSupport(priced=True, supported=supported, region_available=region_available)


@pytest.mark.parametrize(
    "support, call_types, blocked, problems",
    [
        (_support(), ["ON_DEMAND"], False, []),
        (None, ["ON_DEMAND"], False, [UNKNOWN]),
        (_support(supported=None), ["ON_DEMAND"], False, [UNKNOWN]),
        (_support(supported=False), ["ON_DEMAND"], False, [UNSUPPORTED]),
        (_support(region_available=False), ["ON_DEMAND"], False, [UNAVAILABLE]),
        (_support(region_available=None), ["ON_DEMAND"], False, []),
        (_support(), ["PROVISIONED"], False, [PROVISIONED]),
        (_support(), ["INFERENCE_PROFILE"], False, []),
        (_support(), ["ON_DEMAND"], True, [BLOCKED]),
        (_support(supported=False, region_available=False), ["PROVISIONED"], True,
         [UNSUPPORTED, UNAVAILABLE, PROVISIONED, BLOCKED]),
    ],
)
def test_enable_problems(registry_db, support, call_types, blocked, problems):
    availability = _availability(registry_db, call_types)
    model = DBRegistryModel(model_id=CLAUDE, blocked=blocked)

    assert enable_problems(model, "bedrock", support, availability, "us-east-1", f"bedrock/{CLAUDE}") == problems


def test_support_rows_reads_only_the_given_models_and_proxies(registry_db, proxy_region):
    other = DBRegion(name="local-eu1", litellm_api_url="http://eu", litellm_api_key="sk", is_active=True)
    registry_db.add(other)
    first, second = add_model(registry_db), add_model(registry_db, model_id="anthropic.other-v1:0")
    registry_db.add_all(
        DBRegistryModelSupport(model_id=m.id, region_id=r.id, priced=True, checked_at=CHECKED_AT)
        for m in (first, second)
        for r in (proxy_region, other)
    )
    registry_db.flush()

    rows = support_rows(registry_db, [first.id], [proxy_region.id, other.id])

    assert set(rows) == {(first.id, proxy_region.id), (first.id, other.id)}
    assert support_rows(registry_db, [], [proxy_region.id]) == {}


@pytest.mark.parametrize(
    "eol_date, refused",
    [(date(2026, 6, 2), True), (TODAY, False), (date(2026, 5, 31), False), (None, False)],
)
def test_removal_problem(eol_date, refused):
    problem = removal_problem(DBRegistryModel(model_id=CLAUDE, eol_date=eol_date), TODAY)

    if refused:
        assert problem == f"the model reaches its EOL date on {eol_date}; use force to remove it before that date"
    else:
        assert problem is None
