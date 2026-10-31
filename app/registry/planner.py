"""Plan the proxy steps of a registry write: compare the wanted registry state with the live proxy.

Pure, with no database and no HTTP, so a dry run and the sync can share it.
"""

from dataclasses import dataclass, field

from app.registry.litellm import without_alias_copies, without_secrets
from app.registry.prices import price_fields
from app.registry.rules import credential_provider

DEPLOYMENT_FIELDS = frozenset({"model_name", "litellm_model", "credential", "access_groups"})
# `{proxy}` is the literal path text: the planner keeps the proxy name out of the shared message.
ADOPT_IMPORT = "the adopt import (POST /registry/proxies/{proxy}/import)"
ALREADY_RUNS = f"the proxy already runs this deployment; {ADOPT_IMPORT} adds it"
NOT_ON_PROXY = "the deployment is not on the proxy"
PROVISIONED_ONLY = "the deployment is a provisioned-only Bedrock variant; the sync writes no per-token price into it"
DELETES_EVERY_ALIAS = f"this would delete every live alias; {ADOPT_IMPORT} must read them first"
NOT_ADOPTED = (
    "the adopt import has not read the live aliases of the proxy, so the full map could delete them; "
    "a write to a registry-managed proxy that it can read imports the proxy first, and a dry run shows this "
    f"step as blocked until then; otherwise run {ADOPT_IMPORT}"
)
# Doubled braces: the alias name goes in with format(), and {proxy} stays literal.
REMOVES_LIVE_ALIAS = (
    "the live alias {} is not in the registry, so the sync would remove it; "
    "the adopt import (POST /registry/proxies/{{proxy}}/import) must read it first"
)
CREDENTIAL_NOT_ON_PROXY = "the credential {} is not on the proxy"
NOT_MANAGED = "the deployment comes from the proxy config file; the sync does not change or delete it"
LIVE_NAME_CLASH = (
    "the proxy runs another model as {}, and the registry does not know that deployment; run the adopt import first"
)
CANNOT_CLEAR_CREDENTIAL = (
    "LiteLLM keeps a credential name that an update leaves out, and the sync never creates a deployment again"
)
# The cost fields that LiteLLM removes on a null in a model PATCH: its mirrored pricing params (v1.102.1). It
# drops a null for any other field and keeps the old value, so the sync cannot clear those.
LITELLM_CLEARABLE_COSTS = frozenset({
    "input_cost_per_token", "output_cost_per_token", "input_cost_per_character", "output_cost_per_character",
    "cache_read_input_token_cost", "cache_creation_input_token_cost",
})
_TOKEN_PAIR = frozenset({"input_cost_per_token", "output_cost_per_token"})
UNCLEARABLE_COSTS = (
    "the proxy has the cost fields {} that the registry price does not have, and LiteLLM cannot clear them "
    "with an update; remove them on the proxy by hand"
)
RECREATE = "the deployment is gone from the proxy; the sync creates it again"
CREDENTIAL_IN_USE = "a live deployment on the proxy uses the credential {}, so the sync does not delete it"
NO_VALUES = "the registry has no values for the credential {}, so the sync cannot push it"
NO_CREDENTIAL = "the deployment names no credential, and the registry has no {} credential for this proxy"
# model_info.managed_by of every deployment that the sync creates.
MANAGED_BY = "amazee-registry"


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
    # The price fields from pick_price(). An update compares them only when `fields` has "prices".
    price: dict | None = None
    provisioned_only: bool = False
    # The registry_model_regions id, or None for a row that the write deleted.
    row_id: int | None = None
    # True when the registry created the deployment, so the sync may create it again when it is gone.
    recreate: bool = False
    # The stored litellm_params, which hold no secrets.
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Credential:
    """The registry state of one credential on one proxy after the write. Never its values."""

    name: str
    info: dict
    # False for a credential that the adopt import read: the sync never pushes it.
    has_values: bool
    # True when the write changed the values, so the sync sends them again.
    rotate: bool = False
    # False when the write removed the credential from the proxy.
    keep: bool = True


@dataclass(frozen=True)
class LiveProxy:
    deployments: list = field(default_factory=list)
    aliases: dict = field(default_factory=dict)
    credentials: frozenset = frozenset()


def _step(proxy, action, deployment=None, payload=None, changes=None, problems=(), notes=()) -> dict:
    """One step. `problems` stop the sync from sending the step; `notes` only inform."""
    return {
        "proxy": proxy,
        "action": action,
        "model": deployment.model if deployment else None,
        "model_name": deployment.model_name if deployment else None,
        "deployment_id": deployment.deployment_id if deployment else None,
        "payload": payload,
        "changes": changes,
        "problems": [*problems, *notes],
        "row_id": deployment.row_id if deployment else None,
        "blocked": bool(problems),
        "credential": None,
    }


def _name_clash(name: str, litellm_model: str, rows: list[dict], known_ids: frozenset, own_id=None) -> bool:
    """True when a live deployment that the registry does not know runs another model under the name."""
    return any(
        row.get("model_name") == name
        and (row.get("litellm_params") or {}).get("model") != litellm_model
        and (row.get("model_info") or {}).get("id") not in known_ids | {own_id}
        for row in rows
    )


def _credential_step(proxy: str, credential: Credential, live: frozenset, rows: list[dict]) -> dict | None:
    # The payload never holds values: the sync decrypts them when it sends the step.
    payload = {"credential_name": credential.name, "credential_info": credential.info}
    if not credential.keep:
        action = "delete_credential" if credential.name in live else None
    elif credential.name not in live:
        action = "create_credential"
    else:
        action = "update_credential" if credential.rotate else None
    if action is None:
        return None
    problems = [NO_VALUES.format(credential.name)] if action != "delete_credential" and not credential.has_values else []
    # A deployment that the registry does not know, or one added by hand, can still name the credential.
    if action == "delete_credential" and any(
        (row.get("litellm_params") or {}).get("litellm_credential_name") == credential.name for row in rows
    ):
        problems.append(CREDENTIAL_IN_USE.format(credential.name))
    step = _step(proxy, action, payload=None if action == "delete_credential" else payload, problems=problems)
    step["credential"] = credential.name
    return step


def _create_payload(deployment: Deployment) -> dict:
    # The stored params first, so the price, the model and the credential that the registry sets win. A stored
    # cost field is left out: the create sends the wanted price only, or the next repair would clear it.
    stored_costs = price_fields(deployment.params).keys()
    params = {
        key: value
        for key, value in deployment.params.items()
        if key not in ("model", "litellm_credential_name") and key not in stored_costs
    }
    if deployment.price and not deployment.provisioned_only:
        params.update(deployment.price)
    params["model"] = deployment.litellm_model
    if deployment.credential:
        params["litellm_credential_name"] = deployment.credential
    return {
        "model_name": deployment.model_name,
        "litellm_params": params,
        "model_info": {"access_groups": sorted(deployment.access_groups), "managed_by": MANAGED_BY},
    }


def _create(
    proxy: str, deployment: Deployment, live: LiveProxy, rows: list[dict], known_ids: frozenset,
    credential_providers: frozenset,
) -> dict:
    payload = _create_payload(deployment)
    problems, notes = [], []
    if deployment.credential and deployment.credential not in live.credentials:
        problems.append(CREDENTIAL_NOT_ON_PROXY.format(deployment.credential))
    if any(
        row.get("model_name") == deployment.model_name
        and (row.get("litellm_params") or {}).get("model") == deployment.litellm_model
        for row in rows
    ):
        problems.append(ALREADY_RUNS)
    if _name_clash(deployment.model_name, deployment.litellm_model, rows, known_ids):
        problems.append(LIVE_NAME_CLASH.format(deployment.model_name))
    if deployment.deployment_id is not None:
        notes.append(RECREATE)
    if deployment.provisioned_only:
        notes.append(PROVISIONED_ONLY)
    provider = credential_provider(deployment.model.partition("/")[0])
    if not deployment.credential and provider not in credential_providers:
        notes.append(NO_CREDENTIAL.format(provider))
    return _step(proxy, "create_model", deployment, payload=payload, problems=problems, notes=notes)


def _update(
    proxy: str, deployment: Deployment, row: dict, fields: frozenset, credentials: frozenset, rows: list[dict],
    known_ids: frozenset,
) -> dict | None:
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
    problems, notes, stale = [], [], []
    # Only a step that changes the credential names it, so a price step does not repeat an old problem.
    if "credential" in changes and deployment.credential and deployment.credential not in credentials:
        problems.append(CREDENTIAL_NOT_ON_PROXY.format(deployment.credential))
    if "credential" in changes and deployment.credential is None:
        problems.append(CANNOT_CLEAR_CREDENTIAL)
    if "model_name" in changes and _name_clash(
        deployment.model_name, deployment.litellm_model, rows, known_ids, (row.get("model_info") or {}).get("id")
    ):
        problems.append(LIVE_NAME_CLASH.format(deployment.model_name))
    if "prices" in fields and deployment.price is not None:
        # A price lives in litellm_params on the proxy. Every live cost field counts: one that the wanted price
        # does not have would still price the calls, so the update clears it with a null.
        extra = price_fields(params).keys() - deployment.price.keys()
        clearable = LITELLM_CLEARABLE_COSTS
        if len(_TOKEN_PAIR & deployment.price.keys()) == 1:
            # A manual scope can set one side of the per-token pair only; the other side stays on the proxy.
            clearable = clearable - _TOKEN_PAIR
        wanted_price = {**deployment.price, **{name: None for name in extra & clearable}}
        live_price = {name: params.get(name) for name in wanted_price}
        if live_price != wanted_price:
            if deployment.provisioned_only:
                notes.append(PROVISIONED_ONLY)
            else:
                changes["prices"] = {"from": live_price, "to": wanted_price}
        kept = [] if deployment.provisioned_only else sorted(extra - LITELLM_CLEARABLE_COSTS)
        if kept and _replaceable(deployment, row):
            # LiteLLM cannot clear these, so a deployment that the sync made is created again with the wanted
            # params: the sync creates the new one first, then deletes this one.
            changes["unclearable_costs"] = {"from": {name: params[name] for name in kept}, "to": None}
            changes.pop("prices", None)
            if not _managed(row):
                problems.insert(0, NOT_MANAGED)
            return _step(
                proxy, "replace_model", deployment, payload=_create_payload(deployment), changes=changes,
                problems=problems, notes=notes,
            )
        if kept:
            notes.append(UNCLEARABLE_COSTS.format(", ".join(kept)))
            stale = kept
    if not changes and not problems and not notes:
        return None
    if changes and not _managed(row):
        problems.insert(0, NOT_MANAGED)
    step = _step(proxy, "update_model", deployment, changes=changes, problems=problems, notes=notes)
    # The live cost fields that still bill after this step, for the deployment response.
    if stale:
        step["stale_cost_fields"] = stale
    return step


def _inline_secrets(params: dict) -> bool:
    return without_secrets(params).keys() != params.keys()


def _replaceable(deployment: Deployment, row: dict) -> bool:
    """True for a deployment that the sync made, with a named credential and no inline secret anywhere: a new
    create has every value it needs, so nothing is lost when the old one goes."""
    params = row.get("litellm_params") or {}
    return (
        (row.get("model_info") or {}).get("managed_by") == MANAGED_BY
        and bool(params.get("litellm_credential_name"))
        and not _inline_secrets(params)
        and not _inline_secrets(deployment.params)
    )


def _managed(row: dict) -> bool:
    """Only a deployment in the proxy database can be changed; one from the config file cannot."""
    return (row.get("model_info") or {}).get("db_model") is True


def alias_entry(value) -> tuple | None:
    """(target, hidden) of an alias map value, in either LiteLLM form."""
    if isinstance(value, dict):
        return value.get("model"), bool(value.get("hidden"))
    return None if value is None else (value, False)


def _alias_step(proxy: str, live: dict, wanted: dict, removed_alias: str | None, adopted: bool) -> dict | None:
    changes = {
        name: {"from": live.get(name), "to": wanted.get(name)}
        for name in sorted(live.keys() | wanted.keys())
        if alias_entry(live.get(name)) != alias_entry(wanted.get(name))
    }
    if not changes:
        return None
    # Deleting the last alias on purpose leaves an empty map, and that is no problem.
    problems = [DELETES_EVERY_ALIAS] if not wanted and live.keys() - {removed_alias} else []
    if not adopted:
        problems.append(NOT_ADOPTED)
    # The full map drops every live alias that the registry does not have, not only the one a DELETE names.
    # The registry owns the map, so this only informs.
    notes = [
        REMOVES_LIVE_ALIAS.format(name) for name in sorted(live.keys() - wanted.keys()) if name != removed_alias
    ]
    # The sync always sends the full map, so the payload is the full map.
    return _step(proxy, "write_alias_map", payload=dict(wanted), changes=changes, problems=problems, notes=notes)


def plan(
    proxy: str,
    live: LiveProxy,
    deployments=(),
    fields: frozenset = DEPLOYMENT_FIELDS,
    aliases: dict | None = None,
    removed_alias: str | None = None,
    known_ids: frozenset = frozenset(),
    credentials=(),
    credential_providers: frozenset = frozenset(),
    aliases_adopted: bool = True,
) -> list[dict]:
    """The steps that bring the proxy to the wanted state. Only the names in `fields` are compared.

    `aliases` is the wanted full alias map in the LiteLLM form, or None when the write changes no alias.
    `removed_alias` is the alias that the write deletes on purpose, so its removal is no problem.
    `known_ids` are the LiteLLM deployment ids that the registry holds on the proxy.
    `credentials` come first, so a deployment can use a credential that the same plan creates.
    `credential_providers` are the providers of the registry credentials on the proxy, mapped for rule 6.
    `aliases_adopted` is False until the adopt import read the live aliases; the alias step is blocked until then.
    A step with `blocked` set is never sent.
    """
    rows, _ = without_alias_copies(live.deployments, set(live.aliases))
    by_id = {(row.get("model_info") or {}).get("id"): row for row in rows}
    by_id.pop(None, None)
    steps = []
    present = set(live.credentials)
    for credential in sorted(credentials, key=lambda credential: credential.name):
        if step := _credential_step(proxy, credential, live.credentials, rows):
            steps.append(step)
            if step["action"] == "create_credential" and not step["blocked"]:
                present.add(credential.name)
    live = LiveProxy(live.deployments, live.aliases, frozenset(present))
    for deployment in deployments:
        row = by_id.get(deployment.deployment_id)
        if not deployment.keep:
            if row is not None:
                problems = [] if _managed(row) else [NOT_MANAGED]
                steps.append(_step(proxy, "delete_model", deployment, problems=problems))
        elif deployment.deployment_id is None or (row is None and deployment.recreate):
            steps.append(_create(proxy, deployment, live, rows, known_ids, credential_providers))
        elif row is None:
            steps.append(_step(proxy, "update_model", deployment, changes={}, problems=[NOT_ON_PROXY]))
        elif step := _update(proxy, deployment, row, fields, live.credentials, rows, known_ids):
            steps.append(step)
    if aliases is not None and (step := _alias_step(proxy, live.aliases, aliases, removed_alias, aliases_adopted)):
        steps.append(step)
    return steps
