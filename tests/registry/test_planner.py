from dataclasses import replace

import pytest

from app.registry.planner import (
    ALREADY_RUNS,
    CANNOT_CLEAR_CREDENTIAL,
    CREDENTIAL_IN_USE,
    DELETES_EVERY_ALIAS,
    LIVE_NAME_CLASH,
    MANAGED_BY,
    NO_CREDENTIAL,
    NO_VALUES,
    NOT_MANAGED,
    NOT_ON_PROXY,
    PROVISIONED_ONLY,
    RECREATE,
    REMOVES_LIVE_ALIAS,
    UNCLEARABLE_COSTS,
    Credential,
    Deployment,
    LiveProxy,
    plan,
)

PROXY = "local-us1"
GPT = Deployment(
    model="openai/gpt-4o", model_name="gpt-4o", litellm_model="openai/gpt-4o",
    credential="c1", access_groups=("beta",), deployment_id="d1",
)


def _live_row(
    dep_id="d1", name="gpt-4o", model="openai/gpt-4o", credential="c1", groups=("beta",), db_model=True, **params
):
    litellm_params = {"model": model, **params}
    if credential:
        litellm_params["litellm_credential_name"] = credential
    info = {"id": dep_id, "access_groups": list(groups), "db_model": db_model}
    return {"model_name": name, "litellm_params": litellm_params, "model_info": info}


def _step(action, deployment=None, payload=None, changes=None, problems=(), blocked=None):
    return {
        "proxy": PROXY, "action": action, "model": deployment and deployment.model,
        "model_name": deployment and deployment.model_name, "deployment_id": deployment and deployment.deployment_id,
        "payload": payload, "changes": changes, "problems": list(problems),
        "row_id": deployment and deployment.row_id, "blocked": bool(problems) if blocked is None else blocked,
        "credential": None,
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
            "model_info": {"access_groups": ["beta"], "managed_by": MANAGED_BY},
        },
    )]


def test_create_with_no_credential_sends_no_credential_name():
    [step] = plan(PROXY, LiveProxy(), [_new(credential=None, access_groups=())])

    assert step["payload"]["litellm_params"] == {"model": "openai/gpt-4o"}
    assert step["payload"]["model_info"] == {"access_groups": [], "managed_by": MANAGED_BY}
    assert (step["problems"], step["blocked"]) == ([NO_CREDENTIAL.format("openai")], False)


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
    live = LiveProxy(deployments=[row], credentials=frozenset({"c1"}))

    assert plan(PROXY, live, [GPT]) == [_step("update_model", GPT, changes=changes)]


def test_update_to_a_missing_credential_has_a_problem():
    [step] = plan(PROXY, LiveProxy(deployments=[_live_row(credential="c0")], credentials=frozenset({"c0"})), [GPT])

    assert step["changes"] == {"credential": {"from": "c0", "to": "c1"}}
    assert step["problems"] == ["the credential c1 is not on the proxy"]


def test_update_that_keeps_a_missing_credential_has_no_credential_problem():
    row = _live_row(name="gpt", input_cost_per_token=1e-06)
    price = {"input_cost_per_token": 2e-06}

    [name_step] = plan(PROXY, LiveProxy(deployments=[row]), [GPT])
    [price_step] = plan(PROXY, LiveProxy(deployments=[row]), [replace(GPT, price=price)], frozenset({"prices"}))

    assert name_step["problems"] == price_step["problems"] == []


def test_access_groups_in_another_order_are_the_same_groups():
    # The database collation sorts `teama` first; Python sorts `team-z` first.
    deployment = replace(GPT, access_groups=("teama", "team-z"))

    assert plan(PROXY, LiveProxy(deployments=[_live_row(groups=("team-z", "teama"))]), [deployment]) == []
    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [replace(deployment, deployment_id=None)])
    assert step["payload"]["model_info"]["access_groups"] == ["team-z", "teama"]
    [step] = plan(PROXY, LiveProxy(deployments=[_live_row(groups=("teama",))]), [deployment])
    assert step["changes"] == {"access_groups": {"from": ["teama"], "to": ["team-z", "teama"]}}


def test_update_compares_only_the_given_fields():
    row = _live_row(name="gpt", groups=())

    [step] = plan(PROXY, LiveProxy(deployments=[row]), [GPT], frozenset({"access_groups"}))

    assert step["changes"] == {"access_groups": {"from": [], "to": ["beta"]}}


def test_update_of_a_deployment_that_is_not_on_the_proxy():
    assert plan(PROXY, LiveProxy(deployments=[_live_row(dep_id="d2")]), [GPT]) == [
        _step("update_model", GPT, changes={}, problems=[NOT_ON_PROXY])
    ]


def test_price_change_also_clears_a_live_cost_field_that_the_price_does_not_have():
    row = _live_row(input_cost_per_token=1e-06, output_cost_per_token=4e-06, cache_read_input_token_cost=1e-07)
    price = {"input_cost_per_token": 2e-06, "output_cost_per_token": 4e-06}

    steps = plan(PROXY, LiveProxy(deployments=[row]), [replace(GPT, price=price)],
                 frozenset({"prices"}))

    assert [s["changes"] for s in steps] == [{"prices": {
        "from": {"input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06, "cache_read_input_token_cost": 1e-07},
        "to": {**price, "cache_read_input_token_cost": None},
    }}]


def test_price_change_with_no_price_or_the_same_price_gives_no_step():
    row = _live_row(input_cost_per_token=1e-06)
    same = replace(GPT, price={"input_cost_per_token": 1e-06})

    assert plan(PROXY, LiveProxy(deployments=[row]), [GPT, same], frozenset({"prices"})) == []


def test_price_change_of_a_provisioned_only_variant_has_a_problem():
    deployment = replace(GPT, price={"input_cost_per_token": 2e-06}, provisioned_only=True)

    assert plan(PROXY, LiveProxy(deployments=[_live_row()]), [deployment], frozenset({"prices"})) == [
        _step("update_model", deployment, changes={}, problems=[PROVISIONED_ONLY], blocked=False)
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


def test_alias_map_lists_added_changed_and_removed_aliases():
    live = LiveProxy(
        aliases={"chat": "gpt-4o", "old": "gpt-3", "fast": {"model": "gpt-4o", "hidden": True}, "same": "x"}
    )
    wanted = {"chat": "gpt-5", "new": {"model": "gpt-4o", "hidden": True}, "fast": "gpt-4o", "same": "x"}

    assert plan(PROXY, live, aliases=wanted) == [_step("write_alias_map", payload=wanted, changes={
        "chat": {"from": "gpt-4o", "to": "gpt-5"},
        "fast": {"from": {"model": "gpt-4o", "hidden": True}, "to": "gpt-4o"},
        "new": {"from": None, "to": {"model": "gpt-4o", "hidden": True}},
        "old": {"from": "gpt-3", "to": None},
    }, problems=[REMOVES_LIVE_ALIAS.format("old")], blocked=False)]


def test_alias_map_warns_for_each_live_alias_it_removes():
    live = LiveProxy(aliases={"a": "gpt-4o", "b": "gpt-4o", "keep": "gpt-4o"})

    [step] = plan(PROXY, live, aliases={"keep": "gpt-4o", "new": "gpt-4o"})

    assert step["problems"] == [REMOVES_LIVE_ALIAS.format("a"), REMOVES_LIVE_ALIAS.format("b")]


def test_alias_map_does_not_warn_for_the_alias_the_write_deletes():
    live = LiveProxy(aliases={"chat": "gpt-4o", "keep": "gpt-4o"})

    [step] = plan(PROXY, live, aliases={"keep": "gpt-4o"}, removed_alias="chat")

    assert step == _step("write_alias_map", payload={"keep": "gpt-4o"}, changes={"chat": {"from": "gpt-4o", "to": None}})


def test_alias_map_gives_no_step_when_equal():
    # A visible alias in the dict form is the same alias as its target string.
    live = LiveProxy(aliases={"chat": {"model": "gpt-4o", "hidden": False}})

    assert plan(PROXY, live, aliases={"chat": "gpt-4o"}) == []
    assert plan(PROXY, LiveProxy(), aliases={}) == []


def test_an_empty_alias_map_that_removes_live_aliases_has_a_problem():
    [step] = plan(PROXY, LiveProxy(aliases={"chat": "gpt-4o"}), aliases={})

    assert step == _step(
        "write_alias_map", payload={}, changes={"chat": {"from": "gpt-4o", "to": None}},
        problems=[DELETES_EVERY_ALIAS, REMOVES_LIVE_ALIAS.format("chat")],
    )


def test_no_wanted_alias_map_plans_no_alias_step():
    assert plan(PROXY, LiveProxy(aliases={"chat": "gpt-4o"})) == []


PRICE = {"input_cost_per_token": 2e-06, "output_cost_per_token": 8e-06}


def test_create_writes_the_price_and_keeps_the_stored_params():
    deployment = _new(price=PRICE, params={"model": "x", "litellm_credential_name": "c0", "rpm": 10})

    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [deployment])

    assert step["payload"]["litellm_params"] == {
        "rpm": 10, **PRICE, "model": "openai/gpt-4o", "litellm_credential_name": "c1",
    }


def test_create_of_a_provisioned_only_variant_writes_no_price():
    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [_new(price=PRICE, provisioned_only=True)])

    assert step["payload"]["litellm_params"] == {"model": "openai/gpt-4o", "litellm_credential_name": "c1"}
    # The step says why it sends no price, and the note does not stop it.
    assert (step["problems"], step["blocked"]) == ([PROVISIONED_ONLY], False)


def test_a_config_file_deployment_is_not_changed_or_deleted():
    live = LiveProxy(deployments=[_live_row(name="gpt", db_model=None)])

    [update] = plan(PROXY, live, [GPT])
    [delete] = plan(PROXY, live, [replace(GPT, keep=False)])

    assert (update["problems"], update["blocked"]) == ([NOT_MANAGED], True)
    assert (delete["problems"], delete["blocked"]) == ([NOT_MANAGED], True)
    # With nothing to change, there is no step.
    assert plan(PROXY, LiveProxy(deployments=[_live_row(db_model=False)]), [GPT]) == []


def test_create_refuses_a_live_name_that_runs_another_model():
    live = LiveProxy(deployments=[_live_row(dep_id="x9", model="openai/gpt-4")], credentials=frozenset({"c1"}))

    [step] = plan(PROXY, live, [_new()])

    assert step["problems"] == [LIVE_NAME_CLASH.format("gpt-4o")]
    assert step["blocked"] is True
    # A deployment that the registry holds is no clash: another write manages it.
    [known] = plan(PROXY, live, [_new()], known_ids=frozenset({"x9"}))
    assert known["problems"] == []


def test_a_rename_refuses_a_live_name_that_runs_another_model():
    live = LiveProxy(deployments=[_live_row(name="gpt"), _live_row(dep_id="x9", model="openai/gpt-4")])

    [step] = plan(PROXY, live, [GPT], known_ids=frozenset({"d1"}))

    assert step["problems"] == [LIVE_NAME_CLASH.format("gpt-4o")]
    # The same deployment under its old name, and a known id, are no clash.
    assert plan(PROXY, live, [GPT], known_ids=frozenset({"d1", "x9"}))[0]["problems"] == []


def test_a_registry_deployment_that_is_gone_is_created_again():
    deployment = replace(GPT, recreate=True, row_id=7)

    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [deployment])

    assert (step["action"], step["deployment_id"], step["row_id"]) == ("create_model", "d1", 7)
    assert (step["problems"], step["blocked"]) == ([RECREATE], False)


def test_an_adopted_deployment_that_is_gone_is_not_created_again():
    [step] = plan(PROXY, LiveProxy(), [GPT])

    assert (step["action"], step["problems"], step["blocked"]) == ("update_model", [NOT_ON_PROXY], True)


def test_an_update_that_clears_the_credential_is_blocked():
    [step] = plan(PROXY, LiveProxy(deployments=[_live_row()]), [replace(GPT, credential=None)])

    assert step["changes"] == {"credential": {"from": "c1", "to": None}}
    assert (step["problems"], step["blocked"]) == ([CANNOT_CLEAR_CREDENTIAL], True)


AWS = Credential(name="AWS Keys", info={"aws_region_name": "us-east-1"}, has_values=True)


def _credential_step(action, name="AWS Keys", payload=None, problems=()):
    step = _step(action, payload=payload, problems=problems)
    return {**step, "credential": name}


def test_credential_steps_come_first_in_name_order():
    live = LiveProxy(credentials=frozenset({"old", "rotated", "same"}))
    credentials = [
        replace(AWS, name="same"),
        replace(AWS, name="rotated", rotate=True),
        replace(AWS, name="old", keep=False),
        AWS,
        replace(AWS, name="gone", keep=False),
    ]
    payload = {"credential_name": "AWS Keys", "credential_info": {"aws_region_name": "us-east-1"}}

    steps = plan(PROXY, live, [_new(credential="AWS Keys")], credentials=credentials)

    assert steps[:3] == [
        _credential_step("create_credential", payload=payload),
        _credential_step("delete_credential", "old"),
        _credential_step("update_credential", "rotated", {**payload, "credential_name": "rotated"}),
    ]
    # The model create counts the credential that the plan creates as on the proxy.
    assert (steps[3]["action"], steps[3]["problems"]) == ("create_model", [])
    assert all("values" not in str(step["payload"]) for step in steps)


def test_a_credential_that_a_live_deployment_uses_is_not_deleted():
    live = LiveProxy(deployments=[_live_row(credential="AWS Keys")], credentials=frozenset({"AWS Keys"}))

    [step] = plan(PROXY, live, credentials=[replace(AWS, keep=False)])

    assert step["action"] == "delete_credential"
    assert (step["problems"], step["blocked"]) == ([CREDENTIAL_IN_USE.format("AWS Keys")], True)


def test_a_credential_with_no_values_is_never_pushed():
    [step, create] = plan(
        PROXY, LiveProxy(), [_new(credential="AWS Keys")], credentials=[replace(AWS, has_values=False)]
    )

    assert step["action"] == "create_credential"
    assert (step["problems"], step["blocked"]) == ([NO_VALUES.format("AWS Keys")], True)
    assert create["problems"] == ["the credential AWS Keys is not on the proxy"]


def test_a_create_with_no_credential_has_no_note_when_the_proxy_has_a_credential_of_the_provider():
    mantle = Deployment(model="bedrock_mantle/openai.gpt-5.4", model_name="gpt-5.4", litellm_model="bedrock_mantle/openai.gpt-5.4")

    [step] = plan(PROXY, LiveProxy(), [mantle], credential_providers=frozenset({"bedrock"}))
    [other] = plan(PROXY, LiveProxy(), [mantle], credential_providers=frozenset({"openai"}))

    assert step["problems"] == []
    assert other["problems"] == [NO_CREDENTIAL.format("bedrock")]


def test_deleting_the_last_alias_on_purpose_is_no_problem():
    [step] = plan(PROXY, LiveProxy(aliases={"chat": "gpt-4o"}), aliases={}, removed_alias="chat")

    assert (step["payload"], step["problems"], step["blocked"]) == ({}, [], False)


def test_a_price_update_clears_the_live_cost_fields_that_the_wanted_price_does_not_have():
    live_costs = {
        "input_cost_per_token": 1e-06, "output_cost_per_token": 4e-06, "input_cost_per_character": 1e-07,
        "output_cost_per_character": 2e-07, "cache_read_input_token_cost": 1e-08,
        "cache_creation_input_token_cost": 2e-08, "input_cost_per_second": 0.1, "output_cost_per_image": 0.2,
    }
    price = {"input_cost_per_token": 2e-06, "output_cost_per_token": 8e-06}

    [step] = plan(PROXY, LiveProxy(deployments=[_live_row(**live_costs)]), [replace(GPT, price=price)], frozenset({"prices"}))

    assert step["changes"]["prices"]["to"] == {
        **price, "input_cost_per_character": None, "output_cost_per_character": None,
        "cache_read_input_token_cost": None, "cache_creation_input_token_cost": None,
    }
    # LiteLLM keeps these two on a null, so the step names them and does not send them.
    assert step["problems"] == [UNCLEARABLE_COSTS.format("input_cost_per_second, output_cost_per_image")]
    assert step["blocked"] is False


def test_a_price_with_no_extra_live_cost_field_needs_no_step():
    row = _live_row(input_cost_per_token=2e-06, output_cost_per_token=8e-06)

    assert plan(PROXY, LiveProxy(deployments=[row]), [replace(GPT, price=PRICE)], frozenset({"prices"})) == []


def test_a_create_sends_only_the_wanted_price_and_no_stored_cost_field():
    params = {"rpm": 10, "input_cost_per_token": 9e-06, "cache_read_input_token_cost": 1e-08}

    [step] = plan(PROXY, LiveProxy(credentials=frozenset({"c1"})), [_new(price={"input_cost_per_token": 2e-06}, params=params)])

    assert step["payload"]["litellm_params"] == {
        "rpm": 10, "input_cost_per_token": 2e-06, "model": "openai/gpt-4o", "litellm_credential_name": "c1",
    }


def test_a_price_with_one_per_token_field_keeps_the_other_on_the_proxy():
    row = _live_row(input_cost_per_token=1e-06, output_cost_per_token=4e-06, cache_read_input_token_cost=1e-08)

    [step] = plan(PROXY, LiveProxy(deployments=[row]), [replace(GPT, price={"input_cost_per_token": 2e-06})], frozenset({"prices"}))

    # A manual scope can set one side only; the other side is not cleared.
    assert step["changes"]["prices"]["to"] == {"input_cost_per_token": 2e-06, "cache_read_input_token_cost": None}
