from datetime import date

import httpx
import pytest

from app.registry.api.dry_run import ProxyPlan, deployment_plans, plan_steps, read_live
from app.registry.litellm import ProxyClient
from app.registry.models import DBRegistryCloudAvailability, DBRegistryModelRegion
from app.registry.planner import DEPLOYMENT_FIELDS, Deployment, LiveProxy

US_URL = "http://litellm:4000"
EU_URL = "http://litellm-eu:4000"
_REQUEST = httpx.Request("GET", f"{US_URL}/model/info")


@pytest.mark.parametrize(
    "error, reason",
    [
        (httpx.ConnectError("connect to http://litellm:4000 with sk-test failed"), "ConnectError"),
        (httpx.ReadTimeout("sk-test"), "timeout"),
        (httpx.HTTPStatusError("sk-test", request=_REQUEST, response=httpx.Response(500, request=_REQUEST)), "HTTP 500"),
        (ValueError("sk-test"), "bad JSON"),
        (httpx.InvalidURL("sk-test"), "InvalidURL"),
    ],
)
def test_read_live_gives_a_short_reason(live_proxies, error, reason):
    live_proxies.by_url[US_URL] = error

    assert read_live(US_URL, "sk-test") == reason


def test_read_live_of_a_malformed_url_sends_nothing(monkeypatch):
    monkeypatch.setattr("app.registry.api.dry_run.ProxyClient", ProxyClient)
    # A request would fail the test, so the URL must be refused before any request.
    monkeypatch.setattr(httpx.Client, "send", lambda *args, **kwargs: pytest.fail("a request was sent"))

    assert read_live("http://proxy:notaport", "sk-test") == "InvalidURL"


def test_read_live_refuses_a_model_list_that_is_not_a_list(live_proxies):
    live_proxies.by_url[US_URL] = {"model_info": {"data": []}}

    assert read_live(US_URL, "sk-test") == "bad JSON"


def test_read_live_drops_rows_that_are_not_dicts(live_proxies):
    row = {
        "model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"},
        "model_info": {"id": "us-gpt", "access_groups": ["beta"]},
    }
    live_proxies.by_url[US_URL] = {"model_info": [row, "x"], "aliases": {"chat": "gpt-4o"}, "credentials": ["c1"]}

    assert read_live(US_URL, "sk-test") == LiveProxy([row], {"chat": "gpt-4o"}, frozenset({"c1"}))


def test_read_live_reads_a_bad_params_or_info_value_as_empty(live_proxies):
    gpt = {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"id": "us-gpt"}}
    copy_ = {**gpt, "model_name": "chat"}
    bad = {"model_name": "odd", "litellm_params": "openai/odd", "model_info": ["us-gpt"]}
    live_proxies.by_url[US_URL] = {"model_info": [gpt, bad, copy_], "aliases": {"chat": "gpt-4o"}}
    removed = Deployment(
        model="openai/gpt-4o", model_name="gpt-4o", litellm_model="openai/gpt-4o", deployment_id="us-gpt", keep=False,
    )
    odd = Deployment(model="openai/odd", model_name="odd", litellm_model="openai/odd")

    assert read_live(US_URL, "sk-test").deployments[1] == {
        "model_name": "odd", "litellm_params": {}, "model_info": {"access_groups": []},
    }
    steps = plan_steps([ProxyPlan("local-us1", US_URL, "sk-test", [removed, odd])])

    # The alias copy is still removed, the id still matches, and the bad row is not the same deployment as `odd`.
    assert [(s["action"], s["deployment_id"], s["problems"]) for s in steps] == [
        ("delete_model", "us-gpt", []), ("create_model", None, []),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"model_name": ["gpt-4o"]},
        {"model_info": {"id": ["us-gpt"]}},
        {"model_info": {"id": 7}},
        {"model_info": {"id": "x", "access_groups": "beta"}},
        {"model_info": {"id": "x", "access_groups": ["beta", 1]}},
    ],
)
def test_read_live_refuses_a_value_of_the_wrong_type(live_proxies, bad):
    good = {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}, "model_info": {"id": "us-gpt"}}
    live_proxies.by_url[US_URL] = {"model_info": [good, {"model_name": "odd", **bad}]}

    assert read_live(US_URL, "sk-test") == "bad data"
    assert plan_steps([ProxyPlan("local-us1", US_URL, "sk-test")])[0]["problems"] == [
        "the proxy could not be read: bad data"
    ]


@pytest.mark.parametrize("info", [{"id": "x"}, {"id": "x", "access_groups": None}, {"id": None}])
def test_read_live_reads_no_access_groups_as_empty(live_proxies, info):
    live_proxies.by_url[US_URL] = {"model_info": [{"model_name": None, "model_info": info}]}

    [row] = read_live(US_URL, "sk-test").deployments

    assert row["model_info"]["access_groups"] == []


def test_plan_steps_gives_a_read_step_for_a_proxy_that_fails(live_proxies):
    live_proxies.by_url[EU_URL] = httpx.ConnectError("down")
    new = Deployment(model="openai/gpt-4o", model_name="gpt-4o", litellm_model="openai/gpt-4o")
    plans = [
        ProxyPlan("local-us1", US_URL, "sk-test", [new]),
        ProxyPlan("local-eu1", EU_URL, "sk-test", [new]),
    ]

    steps = plan_steps(plans)

    assert steps[0] == {
        "proxy": "local-eu1", "action": "read_proxy", "problems": ["the proxy could not be read: ConnectError"],
    }
    assert [(s["proxy"], s["action"]) for s in steps[1:]] == [("local-us1", "create_model")]
    assert "sk-test" not in repr(plans)


def _rows(db):
    return db.query(DBRegistryModelRegion).order_by(DBRegistryModelRegion.litellm_deployment_id).all()


def test_deployment_plans_give_one_plan_per_proxy(registry_db, deployed):
    plans = {plan.proxy: plan for plan in deployment_plans(registry_db, _rows(registry_db), DEPLOYMENT_FIELDS)}

    assert set(plans) == {"local-eu1", "local-us1"}
    assert (plans["local-us1"].url, plans["local-us1"].key) == (US_URL, "sk-test")
    assert plans["local-eu1"].deployments == [Deployment(
        model="openai/gpt-4o", model_name="gpt-4o", litellm_model="openai/gpt-4o", deployment_id="eu-gpt",
        keep=False,
    )]
    assert [d.deployment_id for d in plans["local-us1"].deployments] == ["us-claude", "us-gpt"]
    assert all(d.keep and d.price is None for d in plans["local-us1"].deployments)


def test_deployment_plans_live_only_and_removed(registry_db, deployed):
    live = deployment_plans(registry_db, _rows(registry_db), DEPLOYMENT_FIELDS, live_only=True)
    assert [plan.proxy for plan in live] == ["local-us1"]

    removed = deployment_plans(registry_db, _rows(registry_db), DEPLOYMENT_FIELDS, removed=True)
    assert all(not d.keep for plan in removed for d in plan.deployments)


def test_deployment_plans_read_the_price_and_the_credential(registry_db, deployed):
    gpt = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-gpt").one()
    gpt.litellm_params = {"model": "openai/gpt-4o", "litellm_credential_name": "c1"}
    registry_db.commit()

    [plan] = deployment_plans(registry_db, [gpt], frozenset({"prices"}))

    assert plan.fields == frozenset({"prices"})
    assert plan.deployments[0].credential == "c1"
    # The base price, because pick_price finds no geo or region price for openai/gpt-4o.
    assert plan.deployments[0].price == {"input_cost_per_token": 2.5e-06}
    assert plan.deployments[0].provisioned_only is False


def test_deployment_plans_find_a_provisioned_only_bedrock_variant(registry_db, deployed):
    claude = registry_db.query(DBRegistryModelRegion).filter_by(litellm_deployment_id="us-claude").one()
    claude.litellm_model = "bedrock/anthropic.claude-x-v1:0"
    registry_db.add(DBRegistryCloudAvailability(
        provider="bedrock", model_id="anthropic.claude-x-v1:0", cloud_region="us-east-1",
        source="bedrock_community", call_types=["PROVISIONED"], last_seen=date(2026, 1, 1),
    ))
    registry_db.commit()

    [plan] = deployment_plans(registry_db, [claude], frozenset({"prices"}))

    assert plan.deployments[0].provisioned_only is True
