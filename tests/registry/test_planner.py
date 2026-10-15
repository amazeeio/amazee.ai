from dataclasses import replace

import pytest

from app.registry.planner import (
    ALREADY_RUNS,
    DELETES_EVERY_ALIAS,
    NOT_ON_PROXY,
    PROVISIONED_ONLY,
    Deployment,
    LiveProxy,
    plan,
)

PROXY = "local-us1"
GPT = Deployment(
    model="openai/gpt-4o", model_name="gpt-4o", litellm_model="openai/gpt-4o",
    credential="c1", access_groups=("beta",), deployment_id="d1",
)


def _live_row(dep_id="d1", name="gpt-4o", model="openai/gpt-4o", credential="c1", groups=("beta",), **params):
    litellm_params = {"model": model, **params}
    if credential:
        litellm_params["litellm_credential_name"] = credential
    info = {"id": dep_id, "access_groups": list(groups)}
    return {"model_name": name, "litellm_params": litellm_params, "model_info": info}


def _step(action, deployment=GPT, payload=None, changes=None, problems=()):
    return {
        "proxy": PROXY, "action": action, "model": deployment.model, "model_name": deployment.model_name,
        "deployment_id": deployment.deployment_id, "payload": payload, "changes": changes, "problems": list(problems),
    }


def _new(**fields):
    return replace(GPT, deployment_id=None, **fields)


def test_create_gives_the_payload():
    deployment = _new()

    assert plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [deployment]) == [_step(
        "create_model", deployment,
        payload={
            "model_name": "gpt-4o",
            "litellm_params": {"model": "openai/gpt-4o", "litellm_credential_name": "c1"},
            "model_info": {"access_groups": ["beta"]},
        },
    )]


def test_create_with_no_credential_sends_no_credential_name():
    [step] = plan(PROXY, LiveProxy(), [_new(credential=None, access_groups=())])

    assert step["payload"]["litellm_params"] == {"model": "openai/gpt-4o"}
    assert step["payload"]["model_info"] == {"access_groups": []}
    assert step["problems"] == []


def test_create_with_a_missing_credential_has_a_problem():
    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c2"})), [_new()])

    assert step["problems"] == ["the credential c1 is not on the proxy"]


def test_create_of_a_deployment_the_proxy_runs_has_a_problem():
    live = LiveProxy(deployments=[_live_row(dep_id="x9")], credentials=frozenset({"c1"}))

    [step] = plan(PROXY, live, [_new()])

    assert step["action"] == "create_model"
    assert step["problems"] == [ALREADY_RUNS]


def test_update_gives_no_step_when_nothing_differs():
    assert plan(PROXY, LiveProxy(deployments=[_live_row()]), [GPT]) == []


@pytest.mark.parametrize(
    "row, changes",
    [
        (_live_row(name="gpt"), {"model_name": {"from": "gpt", "to": "gpt-4o"}}),
        (_live_row(model="openai/gpt-4"), {"litellm_model": {"from": "openai/gpt-4", "to": "openai/gpt-4o"}}),
        (_live_row(credential=None), {"credential": {"from": None, "to": "c1"}}),
        # The live groups are compared as a sorted set.
        (_live_row(groups=("beta", "alpha", "beta")), {"access_groups": {"from": ["alpha", "beta"], "to": ["beta"]}}),
    ],
)
def test_update_lists_each_changed_field(row, changes):
    assert plan(PROXY, LiveProxy(deployments=[row]), [GPT]) == [_step("update_model", changes=changes)]


def test_update_compares_only_the_given_fields():
    row = _live_row(name="gpt", groups=())

    [step] = plan(PROXY, LiveProxy(deployments=[row]), [GPT], frozenset({"access_groups"}))

    assert step["changes"] == {"access_groups": {"from": [], "to": ["beta"]}}


def test_update_of_a_deployment_that_is_not_on_the_proxy():
    assert plan(PROXY, LiveProxy(deployments=[_live_row(dep_id="d2")]), [GPT]) == [
        _step("update_model", changes={}, problems=[NOT_ON_PROXY])
    ]


def test_price_change_compares_only_the_given_keys():
    row = _live_row(input_cost_per_token=1e-06, output_cost_per_token=4e-06, cache_read_input_token_cost=1e-07)
    price = {"input_cost_per_token": 2e-06, "output_cost_per_token": 4e-06}

    steps = plan(PROXY, LiveProxy(deployments=[row]), [replace(GPT, price=price)],
                 frozenset({"prices"}))

    assert [s["changes"] for s in steps] == [{"prices": {
        "from": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06}, "to": price,
    }}]


def test_price_change_with_no_price_or_the_same_price_gives_no_step():
    row = _live_row(input_cost_per_token=1e-06)
    same = replace(GPT, price={"input_cost_per_token": 1e-06})

    assert plan(PROXY, LiveProxy(deployments=[row]), [GPT, same], frozenset({"prices"})) == []


def test_price_change_of_a_provisioned_only_variant_has_a_problem():
    deployment = replace(GPT, price={"input_cost_per_token": 2e-06}, provisioned_only=True)

    assert plan(PROXY, LiveProxy(deployments=[_live_row()]), [deployment], frozenset({"prices"})) == [
        _step("update_model", deployment, changes={}, problems=[PROVISIONED_ONLY])
    ]


def test_delete_when_the_deployment_is_live():
    removed = replace(GPT, keep=False)

    assert plan(PROXY, LiveProxy(deployments=[_live_row()]), [removed]) == [_step("delete_model", removed)]


def test_delete_gives_no_step_when_the_deployment_is_not_live():
    removed = replace(GPT, keep=False)
    new_removed = replace(removed, deployment_id=None)

    assert plan(PROXY, LiveProxy(deployments=[_live_row(dep_id="d2")]), [removed, new_removed]) == []


def test_an_alias_copy_is_not_a_live_deployment():
    # `/model/info` lists the alias `chat` again, with the id of its target.
    live = LiveProxy(
        deployments=[_live_row(name="chat", dep_id="d1"), _live_row(dep_id="d1")], aliases={"chat": "gpt-4o"}
    )

    assert plan(PROXY, live, [GPT]) == []


def test_an_alias_copy_does_not_count_as_a_running_deployment():
    live = LiveProxy(
        deployments=[_live_row(name="gpt-4o", dep_id="d1"), _live_row(name="chat", dep_id="d1")],
        aliases={"chat": "gpt-4o"}, credentials=frozenset({"c1"}),
    )

    [step] = plan(PROXY, live, [_new(model_name="chat")])

    assert step["problems"] == []


def _alias_step(payload, changes, problems=()):
    return {
        "proxy": PROXY, "action": "write_alias_map", "model": None, "model_name": None, "deployment_id": None,
        "payload": payload, "changes": changes, "problems": list(problems),
    }


def test_alias_map_lists_added_changed_and_removed_aliases():
    live = LiveProxy(
        aliases={"chat": "gpt-4o", "old": "gpt-3", "fast": {"model": "gpt-4o", "hidden": True}, "same": "x"}
    )
    wanted = {"chat": "gpt-5", "new": {"model": "gpt-4o", "hidden": True}, "fast": "gpt-4o", "same": "x"}

    assert plan(PROXY, live, aliases=wanted) == [_alias_step(wanted, {
        "chat": {"from": "gpt-4o", "to": "gpt-5"},
        "fast": {"from": {"model": "gpt-4o", "hidden": True}, "to": "gpt-4o"},
        "new": {"from": None, "to": {"model": "gpt-4o", "hidden": True}},
        "old": {"from": "gpt-3", "to": None},
    })]


def test_alias_map_gives_no_step_when_equal():
    # A visible alias in the dict form is the same alias as its target string.
    live = LiveProxy(aliases={"chat": {"model": "gpt-4o", "hidden": False}})

    assert plan(PROXY, live, aliases={"chat": "gpt-4o"}) == []
    assert plan(PROXY, LiveProxy(), aliases={}) == []


def test_an_empty_alias_map_that_removes_live_aliases_has_a_problem():
    [step] = plan(PROXY, LiveProxy(aliases={"chat": "gpt-4o"}), aliases={})

    assert step == _alias_step({}, {"chat": {"from": "gpt-4o", "to": None}}, [DELETES_EVERY_ALIAS])


def test_no_wanted_alias_map_plans_no_alias_step():
    assert plan(PROXY, LiveProxy(aliases={"chat": "gpt-4o"})) == []
