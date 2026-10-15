from datetime import date

import pytest

from app.db.models import DBRegion
from app.registry.config import BEDROCK_GEO_PREFIXES
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModel, DBRegistryModelPrice
from app.registry.rules import (
    NO_PRICE,
    _GEOS_BY_FAMILY,
    _GEOS_BY_REGION,
    backend_problem,
    default_backend,
    enable_problems,
    geo_prefixes,
    UNKNOWN_BEDROCK_REGION,
    geo_problem,
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
        (f"bedrock/{CLAUDE}", "bedrock", CLAUDE, {}),
        (f"bedrock_converse/us.{CLAUDE}", "bedrock", CLAUDE, US),
        (f"bedrock/us-east-1/{CLAUDE}", "bedrock", CLAUDE, US),
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
        (f"bedrock/us.{CLAUDE}", "bedrock", CLAUDE, {}, UNKNOWN_BEDROCK_REGION),
        ("bedrock/us.anthropic.other-v1:0", "bedrock", CLAUDE, US, "the backend is for another model"),
        (f"openai/{CLAUDE}", "bedrock", CLAUDE, US, "the backend is for another model"),
        (CLAUDE, "bedrock", CLAUDE, US, "the backend is for another model"),
        (
            f"bedrock/eu-west-1/{CLAUDE}", "bedrock", CLAUDE, US,
            "the backend names the cloud region eu-west-1, not the proxy's Bedrock region us-east-1",
        ),
        (f"bedrock/us-east-1/{CLAUDE}", "bedrock", CLAUDE, {}, UNKNOWN_BEDROCK_REGION),
    ],
)
def test_backend_problem_refuses(backend, provider, model_id, cloud_regions, problem):
    assert backend_problem(backend, provider, model_id, cloud_regions) == problem


def _availability(db, call_types, region="us-east-1"):
    db.add(
        DBRegistryCloudAvailability(
            provider="bedrock", model_id=CLAUDE, cloud_region=region,
            source="bedrock_community", call_types=call_types, last_seen=date(2026, 9, 27),
        )
    )
    db.flush()
    return Availability(db)


@pytest.mark.parametrize(
    "provider, model_id, cloud_regions, call_types, expected",
    [
        ("bedrock", CLAUDE, US, ["ON_DEMAND", "INFERENCE_PROFILE"], f"bedrock/us.{CLAUDE}"),
        ("bedrock", CLAUDE, US, ["ON_DEMAND"], f"bedrock/{CLAUDE}"),
        # A proxy with no Bedrock cloud region gets the in-region id.
        ("bedrock", CLAUDE, {}, ["INFERENCE_PROFILE"], f"bedrock/{CLAUDE}"),
        ("openai", "gpt-4o", US, ["INFERENCE_PROFILE"], "openai/gpt-4o"),
    ],
)
def test_default_backend(registry_db, provider, model_id, cloud_regions, call_types, expected):
    availability = _availability(registry_db, call_types)

    assert default_backend(provider, model_id, cloud_regions, availability) == expected


@pytest.mark.parametrize(
    "support, call_types, blocked, problems",
    [
        (support_row(), ["ON_DEMAND"], False, []),
        # With no support row, the proxy's price is unknown too.
        (None, ["ON_DEMAND"], False, [UNKNOWN, NO_PRICE]),
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

    assert enable_problems(model, "bedrock", support, availability, "us-east-1", f"bedrock/{CLAUDE}", []) == problems


def _price(scope_kind="base", scope=""):
    return DBRegistryModelPrice(
        scope_kind=scope_kind, scope=scope, prices={"input_cost_per_token": 1e-06}, source="litellm"
    )


@pytest.mark.parametrize(
    "priced, prices, own_prices, backend, problems",
    [
        (True, [], None, f"bedrock/{CLAUDE}", []),
        (False, [_price()], None, f"bedrock/{CLAUDE}", []),
        (False, [_price("cloud_region", "us-east-1")], None, f"bedrock/{CLAUDE}", []),
        (False, [], {"input_cost_per_token": 1e-06}, f"bedrock/{CLAUDE}", []),
        (False, [], None, f"bedrock/{CLAUDE}", [NO_PRICE]),
        (False, [], {}, f"bedrock/{CLAUDE}", [NO_PRICE]),
        # A geo id takes its geo price or the base price, never a cloud region price.
        (False, [_price("cloud_region", "us-east-1")], None, f"bedrock/us.{CLAUDE}", [NO_PRICE]),
        (False, [_price("geo", "us")], None, f"bedrock/us.{CLAUDE}", []),
    ],
)
def test_enable_problems_price(registry_db, priced, prices, own_prices, backend, problems):
    availability = _availability(registry_db, ["ON_DEMAND", "INFERENCE_PROFILE"])
    model = DBRegistryModel(model_id=CLAUDE, blocked=False)
    support = support_row(priced=priced)

    assert enable_problems(model, "bedrock", support, availability, "us-east-1", backend, prices, own_prices) == problems


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
    "eol_date, expected",
    [
        (date(2026, 6, 2), "the model reaches its EOL date on 2026-06-02; use force to remove it before that date"),
        (TODAY, None),
        (date(2026, 5, 31), None),
        (None, None),
    ],
)
def test_removal_problem(eol_date, expected):
    assert removal_problem(DBRegistryModel(model_id=CLAUDE, eol_date=eol_date), TODAY) == expected


def test_needs_profile_with_no_geo_prefix_gives_no_hint(registry_db):
    availability = _availability(registry_db, ["INFERENCE_PROFILE"], "me-central-1")
    model = DBRegistryModel(model_id=CLAUDE, blocked=False)

    problems = enable_problems(model, "bedrock", support_row(), availability, "me-central-1", f"bedrock/{CLAUDE}", [])

    assert problems == ["the proxy's cloud region offers the model only through a geo profile"]


@pytest.mark.parametrize(
    "call_types, region, problems",
    [
        (["PROVISIONED"], "us-east-1", [PROVISIONED]),
        (["ON_DEMAND"], "us-east-1", ["the proxy's cloud region does not offer the model through a geo profile"]),
        (["INFERENCE_PROFILE"], "us-east-1", []),
        (["ON_DEMAND", "INFERENCE_PROFILE"], "us-east-1", []),
        # No call types for the proxy's region: support and region availability decide.
        (["PROVISIONED"], "eu-west-1", []),
    ],
)
def test_enable_problems_of_a_geo_backend(registry_db, call_types, region, problems):
    availability = _availability(registry_db, call_types, region)
    model = DBRegistryModel(model_id=CLAUDE, blocked=False)

    assert enable_problems(model, "bedrock", support_row(), availability, "us-east-1", f"bedrock/us.{CLAUDE}", []) == problems


@pytest.mark.parametrize(
    "backend, provider, cloud_regions, problem",
    [
        ("openai/anthropic.opus", "bedrock_mantle", US, None),
        (f"bedrock/us-east-1/{CLAUDE}", "bedrock", US, None),
        (
            f"bedrock/eu-west-1/{CLAUDE}", "bedrock", US,
            "the backend names the cloud region eu-west-1, not the proxy's Bedrock region us-east-1",
        ),
        (f"bedrock/us.{CLAUDE}", "bedrock", US, None),
        (f"bedrock/eu.{CLAUDE}", "bedrock", US, "the geo prefix eu is not allowed on this proxy"),
        (f"bedrock/global.{CLAUDE}", "bedrock", US, "the geo prefix global is not allowed on this proxy"),
        ("azure/global/gpt-4o", "azure", US, "the geo prefix global is not allowed on this proxy"),
        (f"bedrock/{CLAUDE}", "bedrock", {}, None),
        (f"bedrock/us.{CLAUDE}", "bedrock", {}, UNKNOWN_BEDROCK_REGION),
        (f"bedrock/us-east-1/{CLAUDE}", "bedrock", {}, UNKNOWN_BEDROCK_REGION),
        (f"bedrock/global.{CLAUDE}", "bedrock", {}, "the geo prefix global is not allowed on this proxy"),
        ("azure/eu/gpt-4o", "azure", {}, None),
    ],
)
def test_geo_problem_checks_only_the_region_part(backend, provider, cloud_regions, problem):
    assert geo_problem(backend, provider, cloud_regions) == problem
