from datetime import date

import pytest

from app.db.models import DBRegion
from app.registry.config import BEDROCK_GEO_PREFIXES
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModel
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
from tests.registry.api.conftest import (
    BLOCKED,
    NEEDS_PROFILE,
    PROVISIONED,
    UNAVAILABLE,
    UNKNOWN,
    UNSUPPORTED,
    support_row,
)
from tests.registry.conftest import add_model

CLAUDE = "anthropic.claude-x-v1:0"
US = {"bedrock": "us-east-1"}
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
        ("azure/global/gpt-4o", "azure", "gpt-4o", {}, "the geo prefix global is not allowed on this proxy"),
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


@pytest.mark.parametrize(
    "support, call_types, blocked, problems",
    [
        (support_row(), ["ON_DEMAND"], False, []),
        (None, ["ON_DEMAND"], False, [UNKNOWN]),
        (support_row(supported=None), ["ON_DEMAND"], False, [UNKNOWN]),
        (support_row(supported=False), ["ON_DEMAND"], False, [UNSUPPORTED]),
        (support_row(region_available=False), ["ON_DEMAND"], False, [UNAVAILABLE]),
        (support_row(region_available=None), ["ON_DEMAND"], False, []),
        (support_row(), ["PROVISIONED"], False, [PROVISIONED]),
        (support_row(), ["INFERENCE_PROFILE"], False, [NEEDS_PROFILE]),
        (support_row(), ["ON_DEMAND", "INFERENCE_PROFILE"], False, []),
        (support_row(), ["ON_DEMAND"], True, [BLOCKED]),
        (support_row(supported=False, region_available=False), ["PROVISIONED"], True,
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
        support_row(m.id, r.id)
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


def test_needs_profile_with_no_geo_prefix_gives_no_hint(registry_db):
    registry_db.add(
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=CLAUDE, cloud_region="me-central-1",
            source="bedrock_community", call_types=["INFERENCE_PROFILE"], last_seen=date(2026, 9, 27),
        )
    )
    registry_db.flush()
    model = DBRegistryModel(model_id=CLAUDE, blocked=False)

    problems = enable_problems(
        model, "bedrock", support_row(), Availability(registry_db), "me-central-1", f"bedrock/{CLAUDE}"
    )

    assert problems == ["the proxy's cloud region offers the model only through a geo profile"]
