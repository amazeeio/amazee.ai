"""Plan the proxy steps of a registry write: compare the wanted registry state with the live proxy.

Pure, with no database and no HTTP, so a dry run and the sync can share it.
"""

from dataclasses import dataclass, field

from app.registry.litellm import without_alias_copies

DEPLOYMENT_FIELDS = frozenset({"model_name", "litellm_model", "credential", "access_groups"})
ALREADY_RUNS = "the proxy already runs this deployment; the phase 3 import adopts it"
NOT_ON_PROXY = "the deployment is not on the proxy"
PROVISIONED_ONLY = "the deployment is a provisioned-only Bedrock variant; the sync writes no per-token price into it"
DELETES_EVERY_ALIAS = "this would delete every live alias; the phase 3 import must read them first"


@dataclass(frozen=True)
class Deployment:
    """The registry state of one deployment after the write."""

    model: str
    model_name: str
    litellm_model: str
    credential: str | None = None
    access_groups: tuple[str, ...] = ()
    deployment_id: str | None = None
    # False when the write removed the deployment or turned it off.
    keep: bool = True
    # The price fields from pick_price(), set only by a price write.
    price: dict | None = None
    provisioned_only: bool = False


@dataclass(frozen=True)
class LiveProxy:
    deployments: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)
    credentials: frozenset = frozenset()


def _step(proxy, action, deployment=None, payload=None, changes=None, problems=()) -> dict:
    return {
        "proxy": proxy,
        "action": action,
        "model": deployment.model if deployment else None,
        "model_name": deployment.model_name if deployment else None,
        "deployment_id": deployment.deployment_id if deployment else None,
        "payload": payload,
        "changes": changes,
        "problems": list(problems),
    }


def _create(proxy: str, deployment: Deployment, live: LiveProxy, rows: list[dict]) -> dict:
    params = {"model": deployment.litellm_model}
    if deployment.credential:
        params["litellm_credential_name"] = deployment.credential
    payload = {
        "model_name": deployment.model_name,
        "litellm_params": params,
        "model_info": {"access_groups": sorted(deployment.access_groups)},
    }
    problems = []
    if deployment.credential and deployment.credential not in live.credentials:
        problems.append(f"the credential {deployment.credential} is not on the proxy")
    if any(
        row.get("model_name") == deployment.model_name
        and (row.get("litellm_params") or {}).get("model") == deployment.litellm_model
        for row in rows
    ):
        problems.append(ALREADY_RUNS)
    return _step(proxy, "create_model", deployment, payload=payload, problems=problems)


def _update(proxy: str, deployment: Deployment, row: dict, fields: frozenset) -> dict | None:
    params = row.get("litellm_params") or {}
    live = {
        "model_name": row.get("model_name"),
        "litellm_model": params.get("model"),
        "credential": params.get("litellm_credential_name"),
        "access_groups": sorted(set((row.get("model_info") or {}).get("access_groups") or [])),
    }
    wanted = {
        "model_name": deployment.model_name,
        "litellm_model": deployment.litellm_model,
        "credential": deployment.credential,
        # Python order on both sides: the database sorts slugs by its collation, which can differ.
        "access_groups": sorted(set(deployment.access_groups)),
    }
    changes = {
        name: {"from": live[name], "to": wanted[name]}
        for name in sorted(fields & live.keys())
        if live[name] != wanted[name]
    }
    problems = []
    if "prices" in fields and deployment.price is not None:
        # A price lives in litellm_params on the proxy; compare only the fields the registry sets.
        live_price = {name: params.get(name) for name in deployment.price}
        if live_price != deployment.price:
            if deployment.provisioned_only:
                problems.append(PROVISIONED_ONLY)
            else:
                changes["prices"] = {"from": live_price, "to": deployment.price}
    if not changes and not problems:
        return None
    return _step(proxy, "update_model", deployment, changes=changes, problems=problems)


def _alias(value) -> tuple | None:
    """(target, hidden) of an alias map value, in either LiteLLM form."""
    if isinstance(value, dict):
        return value.get("model"), bool(value.get("hidden"))
    return None if value is None else (value, False)


def _alias_step(proxy: str, live: dict, wanted: dict) -> dict | None:
    changes = {
        name: {"from": live.get(name), "to": wanted.get(name)}
        for name in sorted(live.keys() | wanted.keys())
        if _alias(live.get(name)) != _alias(wanted.get(name))
    }
    if not changes:
        return None
    problems = [DELETES_EVERY_ALIAS] if live and not wanted else []
    # The sync always sends the full map, so the payload is the full map.
    return _step(proxy, "write_alias_map", payload=dict(wanted), changes=changes, problems=problems)


def plan(
    proxy: str,
    live: LiveProxy,
    deployments=(),
    fields: frozenset = DEPLOYMENT_FIELDS,
    aliases: dict | None = None,
) -> list[dict]:
    """The steps that bring the proxy to the wanted state. Only the names in `fields` are compared.

    `aliases` is the wanted full alias map in the LiteLLM form, or None when the write changes no alias.
    """
    rows, _ = without_alias_copies(live.deployments, set(live.aliases))
    by_id = {(row.get("model_info") or {}).get("id"): row for row in rows}
    by_id.pop(None, None)
    steps = []
    for deployment in deployments:
        row = by_id.get(deployment.deployment_id) if deployment.deployment_id else None
        if not deployment.keep:
            if row is not None:
                steps.append(_step(proxy, "delete_model", deployment))
        elif deployment.deployment_id is None:
            steps.append(_create(proxy, deployment, live, rows))
        elif row is None:
            steps.append(_step(proxy, "update_model", deployment, changes={}, problems=[NOT_ON_PROXY]))
        elif step := _update(proxy, deployment, row, fields):
            steps.append(step)
    if aliases is not None and (step := _alias_step(proxy, live.aliases, aliases)):
        steps.append(step)
    return steps
