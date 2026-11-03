"""The sync engine: push the registry state of a sync run to its proxies.

It imports the planning helpers of dry_run.py and never a route module, so a script can import it with no
import cycle. A run saves only ids in its scope and reads the wanted state again after it takes the proxy lock.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import InvalidToken
from fastapi import BackgroundTasks, Response
from sqlalchemy import func, or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, engine
from app.db.models import DBRegion
from app.registry import config, planner
from app.registry.api import dry_run
from app.registry.api.dry_run import (
    ProxyPlan,
    _proxy_plan,
    alias_plans,
    credential_plans,
    credential_providers,
    deployment_plans,
    plan_proxy,
    plan_steps,
    proxy_credentials,
    read_live,
    short_reason,
    unreadable,
)
from app.registry.config import registry_manages
from app.registry.importer import import_proxy
from app.registry.runs import record_run
from app.registry.versions import active_regions
from app.registry.crypto import CANNOT_DECRYPT, MissingKey, decrypt_values
from app.registry.litellm import ProxyWriter, without_alias_copies, without_secrets
from app.registry.models import (
    DBRegistryCredential,
    DBRegistryEvent,
    DBRegistryProxy,
    DBRegistryModelRegion,
    DBRegistrySyncRun,
    DBRegistrySyncStep,
)
from app.registry.schemas import RegistryDryRun, RegistrySyncStarted

logger = logging.getLogger(__name__)

ALREADY_DONE = "the proxy already has this state"
STALE = "the proxy changed after the plan; the next repair plans it again"
LOCK_BUSY = "another sync run holds the proxy"
LOCK_SKIPPED = "another sync run holds the proxy; the next run repairs it"
NOT_MANAGED_BY_REGISTRY = "the registry does not manage the proxy"
_LOCK_TIMEOUT = "55P03"
_MODEL_STEPS = ("create_model", "update_model", "replace_model", "delete_model")
# Steps that create a deployment and read the proxy again for its id.
_CREATES = ("create_model", "replace_model")


def _now() -> datetime:
    return datetime.now(UTC)


def _entry() -> dict:
    return {
        "deployments": [], "deleted": [], "fields": [], "aliases": False, "removed_alias": None,
        "credentials": [], "full": False, "skip_failed": False,
    }


def _save(
    db: Session,
    dry_run: bool,
    result,
    *,
    response: Response | None = None,
    plan: Callable[[], list[ProxyPlan]] | None = None,
    background: BackgroundTasks | None = None,
):
    """Commit the write and return `result`, or in a dry run, roll it back and return it with the planned steps.

    `result` can be a function that builds the body, so the body shows the changed_at that this sets.
    A write that is not a dry run reads only the database: it marks the planned deployments as changed, and for
    a registry-managed proxy it saves a pending sync run in the same commit and answers 202.
    """
    # The flush runs the database checks, so a dry run fails where the write would.
    db.flush()
    plans = plan() if plan else []
    row_ids = [d.row_id for p in plans for d in p.deployments if d.row_id is not None]
    if not dry_run and row_ids:
        # "fetch" also sets the value on the rows in the session, which the body reads.
        db.query(DBRegistryModelRegion).filter(DBRegistryModelRegion.id.in_(row_ids)).update(
            {"changed_at": _now()}, synchronize_session="fetch"
        )
    # A Response is callable too, but it is a finished body.
    if callable(result) and not isinstance(result, Response):
        result = result()
    body = None if isinstance(result, Response) else result
    if dry_run:
        # End the transaction before the proxy reads, so no row lock is held during HTTP.
        db.rollback()
        if response is not None:
            response.status_code = 200
        return RegistryDryRun(result=body, steps=plan_steps(plans))
    # Only a write with a plan passes `background`, and only a plan has proxies.
    managed = [p for p in plans if registry_manages(p.proxy)]
    run = create_run(db, "write", scope_of(managed)) if managed else None
    # One commit, so the write and its pending run are saved together.
    db.commit()
    if run is None:
        return result
    background.add_task(execute_run, run.id)
    if response is not None:
        response.status_code = 202
    return RegistrySyncStarted(sync_run_id=run.id, result=body)


def scope_of(plans: list[ProxyPlan]) -> dict:
    """The scope of a run, keyed by region id as text: ids and names only, so the run reads the state again."""
    scope: dict[str, dict] = {}
    for plan in plans:
        entry = scope.setdefault(str(plan.region_id), _entry())
        for deployment in plan.deployments:
            if deployment.row_id is not None:
                entry["deployments"].append(deployment.row_id)
            elif deployment.deployment_id:
                entry["deleted"].append({
                    "deployment_id": deployment.deployment_id, "model": deployment.model,
                    "model_name": deployment.model_name, "litellm_model": deployment.litellm_model,
                })
        entry["fields"] = sorted(set(entry["fields"]) | plan.fields)
        if plan.aliases is not None:
            entry["aliases"] = True
            entry["removed_alias"] = plan.removed_alias
        entry["credentials"] += [
            {"name": credential.name, "rotate": credential.rotate, "keep": credential.keep}
            for credential in plan.credentials
        ]
    return scope


def full_scope(region_ids, skip_failed: bool = False) -> dict:
    """A scope that repairs every deployment, the alias map and the credentials of each proxy."""
    return {str(region_id): {**_entry(), "full": True, "skip_failed": skip_failed} for region_id in region_ids}


def retry_scope(run: DBRegistrySyncRun, steps: list[DBRegistrySyncStep]) -> dict:
    """The scope of the failed items of a run only. A proxy that could not be read or locked is retried whole."""
    scope = {}
    for region_id in sorted({step.region_id for step in steps if step.status == "failed"}):
        original = {**_entry(), **run.scope.get(str(region_id), {})}
        failed = [step for step in steps if step.region_id == region_id and step.status == "failed"]
        if any(step.action in ("read_proxy", "lock_proxy") for step in failed):
            scope[str(region_id)] = original
            continue
        fields = planner.DEPLOYMENT_FIELDS | {"prices"} if original["full"] else original["fields"]
        failed_ids = {step.deployment_id for step in failed if step.action == "delete_model"}
        # A delete with no row is the old deployment of a replace; the scope has no entry for it yet.
        listed = {item["deployment_id"] for item in original["deleted"]}
        left = [
            {"deployment_id": step.deployment_id, "model": step.model, "model_name": step.model_name, "litellm_model": ""}
            for step in failed
            if step.action == "delete_model" and step.model_region_id is None and step.deployment_id not in listed
        ]
        names = {step.credential for step in failed if step.credential}
        known = {item["name"]: item for item in original["credentials"]}
        scope[str(region_id)] = {
            **_entry(),
            "deployments": sorted({step.model_region_id for step in failed if step.model_region_id is not None}),
            "deleted": [item for item in original["deleted"] if item["deployment_id"] in failed_ids] + left,
            "fields": sorted(fields),
            "aliases": any(step.action == "write_alias_map" for step in failed),
            "removed_alias": original["removed_alias"],
            # A full scope found its credentials itself, so they are not in its entry.
            "credentials": [known.get(name, {"name": name, "rotate": False, "keep": True}) for name in sorted(names)],
        }
    return scope


def scope_plans(db: Session, scope: dict) -> list[ProxyPlan]:
    """The wanted plan of each proxy of a scope, for a dry run."""
    regions = {region.id: region for region in db.query(DBRegion).filter(DBRegion.id.in_([int(k) for k in scope]))}
    return [plans_for_scope(db, regions[int(key)], entry)[0] for key, entry in scope.items() if int(key) in regions]


def plans_for_scope(db: Session, region: DBRegion, entry: dict) -> tuple[ProxyPlan, list[str]]:
    """The wanted state of one proxy for one scope entry, read from the database now, and the model names of
    the deployments that a full scope with `skip_failed` leaves out.

    A full scope covers every deployment, the full alias map and every linked credential of the proxy.
    """
    query = db.query(DBRegistryModelRegion).filter(DBRegistryModelRegion.region_id == region.id)
    left_out = []
    if entry["full"]:
        fields = planner.DEPLOYMENT_FIELDS | {"prices"}
        rows = query.order_by(DBRegistryModelRegion.id).all()
        if entry["skip_failed"]:
            # A deployment whose last step failed waits for an admin; the repair only reports it.
            left_out = sorted({row.model_name for row in rows if row.sync_error is not None})
            rows = [row for row in rows if row.sync_error is None]
        entry = {**entry, "aliases": True, "credentials": [
            {"name": name, "rotate": False, "keep": True} for name in proxy_credentials(db, region.id)
        ]}
    else:
        fields = frozenset(entry["fields"]) or planner.DEPLOYMENT_FIELDS
        rows = query.filter(DBRegistryModelRegion.id.in_(entry["deployments"])).order_by(DBRegistryModelRegion.id).all()
    plan = next(iter(deployment_plans(db, rows, fields)), None) or _proxy_plan(region, fields=fields)
    deleted = [planner.Deployment(**item, keep=False) for item in entry["deleted"]]
    plan = replace(
        plan,
        deployments=[*plan.deployments, *deleted],
        known_ids=dry_run._known_ids(db, [region.id]).get(region.id, frozenset()),
        credential_providers=credential_providers(db, [region.id]).get(region.id, frozenset()),
    )
    if entry["aliases"]:
        wanted = alias_plans(db, region)[0]
        plan = replace(
            plan, aliases=wanted.aliases, removed_alias=entry.get("removed_alias"),
            aliases_adopted=wanted.aliases_adopted,
        )
    if entry["credentials"]:
        # Whether the proxy keeps a credential is read now, under the lock: a write after this run was made, or
        # before its retry, can have linked the proxy again or removed it.
        linked = set(proxy_credentials(db, region.id))
        plan = replace(plan, credentials=tuple(
            credential_plans(
                db, item["name"], [region.id], item["rotate"] and item["name"] in linked, item["name"] in linked
            )[0].credentials[0]
            for item in entry["credentials"]
        ))
    return plan, left_out


def create_run(db: Session, trigger: str, scope: dict) -> DBRegistrySyncRun:
    """Add a pending run. The caller commits it with its write, so the run exists before its task starts."""
    run = DBRegistrySyncRun(
        trigger=trigger, status="pending", region_ids=sorted(int(region_id) for region_id in scope), scope=scope
    )
    db.add(run)
    db.flush()
    return run


def _secret_forms(secret: str) -> set[str]:
    """The forms in which LiteLLM can echo a secret: as is, JSON-escaped, as a Python repr, and line by line."""
    forms = {secret, json.dumps(secret)[1:-1], repr(secret)[1:-1]}
    # A private key is several lines, and an echo can break it at a line end.
    forms |= {line.strip() for line in secret.splitlines()}
    return {form for form in forms if len(form) >= 8}


def _scrub(message: str, secrets) -> str:
    """The message with each secret of 8 characters or more replaced, because LiteLLM can echo a payload."""
    forms = set().union(*(_secret_forms(s) for s in secrets if isinstance(s, str)))
    for form in sorted(forms, key=len, reverse=True):
        message = message.replace(form, "********")
    return message


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return [value] if isinstance(value, str) else []


def _error(exc: Exception, secrets) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        # Scrub before the cut, so a secret across the cut leaves no part behind.
        return _scrub(f"HTTP {exc.response.status_code}: {exc.response.text}", secrets)[:500]
    return type(exc).__name__


class _Lock:
    """A session-level advisory lock on one proxy, on its own connection.

    A pooled session connection can go back to the pool after a commit and keep the lock, so the lock gets a
    connection of its own, and a failed unlock throws that connection away.
    """

    def __init__(self, region_id: int):
        self.key = {"ns": config.SYNC_LOCK_NAMESPACE, "id": region_id}
        self.conn = None

    def acquire(self, wait: bool) -> bool:
        self.conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            if not wait:
                got = self.conn.execute(text("SELECT pg_try_advisory_lock(:ns, :id)"), self.key).scalar()
            else:
                # SET takes no bind parameter, so the timeout is formatted in.
                self.conn.execute(text(f"SET lock_timeout = '{int(config.SYNC_LOCK_WAIT_SECONDS * 1000)}ms'"))
                try:
                    self.conn.execute(text("SELECT pg_advisory_lock(:ns, :id)"), self.key)
                    got = True
                except OperationalError as exc:
                    if getattr(exc.orig, "pgcode", None) != _LOCK_TIMEOUT:
                        raise
                    got = False
                self.conn.execute(text("RESET lock_timeout"))
        except Exception:
            self.conn.invalidate()
            self.conn.close()
            raise
        if not got:
            self.conn.close()
        return got

    def release(self) -> None:
        try:
            self.conn.execute(text("SELECT pg_advisory_unlock(:ns, :id)"), self.key)
        except Exception:
            logger.exception("Could not release the sync lock of region %s", self.key["id"])
            # A connection back in the pool would keep the lock.
            self.conn.invalidate()
        finally:
            self.conn.close()


class _Proxy:
    """One region of a run: the plain values that the steps need, so no ORM object is read during HTTP."""

    def __init__(self, db: Session, run_id: int, region: DBRegion, entry: dict, trigger: str):
        self.db, self.run_id, self.entry, self.trigger = db, run_id, entry, trigger
        self.region_id, self.name = region.id, region.name
        self.url, self.key = region.litellm_api_url, region.litellm_api_key
        self.position = 0

    def record(self, action: str, status: str, message: str | None = None, step: dict | None = None) -> int:
        step = step or {}
        row = DBRegistrySyncStep(
            run_id=self.run_id, region_id=self.region_id, position=self.position, action=action,
            model=step.get("model"), model_name=step.get("model_name"), deployment_id=step.get("deployment_id"),
            model_region_id=step.get("row_id"), credential=step.get("credential"),
            payload=step.get("payload"), changes=step.get("changes"), status=status, message=message,
            finished_at=None if status == "pending" else _now(),
        )
        self.position += 1
        self.db.add(row)
        self.db.flush()
        if status != "pending":
            logger.info("Sync run %s %s %s: %s", self.run_id, self.name, action, status)
        return row.id

    def finish(self, step_id: int, action: str, status: str, message: str | None) -> None:
        self.db.query(DBRegistrySyncStep).filter_by(id=step_id).update(
            {"status": status, "message": message, "finished_at": _now()}, synchronize_session=False
        )
        logger.info("Sync run %s %s %s: %s", self.run_id, self.name, action, status)


def _set_row(db: Session, row_id: int | None, **values) -> int:
    """A targeted UPDATE of the sync result columns, so a concurrent API write to other columns stays."""
    if row_id is None:
        return 0
    return db.query(DBRegistryModelRegion).filter_by(id=row_id).update(values, synchronize_session=False)


def _price_columns(price: dict) -> dict:
    """The deployment's own price columns for a price that is now in its litellm_params on the proxy."""
    return {
        "prices": dict(price),
        "input_cost_per_token": price.get("input_cost_per_token"),
        "output_cost_per_token": price.get("output_cost_per_token"),
    }


def _mark_in_sync(
    db: Session, row_id: int | None, read_at: datetime, removed: frozenset = frozenset(), price: dict | None = None
) -> None:
    """Record that a deployment matches the proxy, only when it did not before, so a run that finds no drift
    changes nothing. A removed deployment that the proxy no longer runs also loses its LiteLLM id. `price` is
    the compared price that the proxy has."""
    if row_id is None:
        return
    if row_id in removed:
        _set_row(db, row_id, litellm_deployment_id=None)
    values = {"synced_at": read_at, "sync_error": None, **(_price_columns(price) if price is not None else {})}
    db.query(DBRegistryModelRegion).filter(
        DBRegistryModelRegion.id == row_id,
        or_(
            DBRegistryModelRegion.synced_at.is_(None),
            DBRegistryModelRegion.sync_error.isnot(None),
            DBRegistryModelRegion.changed_at > DBRegistryModelRegion.synced_at,
        ),
    ).update(values, synchronize_session=False)


def _hand_edit(before: tuple | None, deployment: planner.Deployment | None, step: dict) -> bool:
    """True when only the live side moved: the row was in sync, and the registry wants what it pushed last time.

    API writes set changed_at, so an in-sync row has no registry-side change but a new picked price. The row's
    prices hold the price that the sync put on the proxy last, so an equal wanted price means the proxy moved.
    """
    if before is None or deployment is None:
        return False
    synced_at, changed_at, sync_error, prices = before
    if synced_at is None or sync_error is not None or (changed_at is not None and changed_at > synced_at):
        return False
    # A replace comes from the price as well: the wanted price must be the one pushed last time.
    price_step = "prices" in (step["changes"] or {}) or step["action"] == "replace_model"
    return not price_step or (prices or {}) == (deployment.price or {})


def _record_repair(proxy: _Proxy, step: dict) -> None:
    """A proxy.drift_repaired event for a deployment that someone changed on the proxy by hand."""
    found = proxy.db.query(DBRegistryModelRegion.model_id, DBRegistryModelRegion.litellm_deployment_id).filter_by(
        id=step["row_id"]
    ).first()
    if found is None:
        return
    proxy.db.add(DBRegistryEvent(
        type="proxy.drift_repaired", model_id=found[0], region_id=proxy.region_id,
        data={
            "action": step["action"], "model_name": step["model_name"],
            "deployment_id": step["deployment_id"] or found[1], "fields": sorted(step["changes"] or {}),
            "sync_run_id": proxy.run_id,
        },
    ))


def _sent_price(step: dict) -> dict | None:
    """The price fields that a create sent, for a write run that compared no prices."""
    if step["action"] not in _CREATES:
        return None
    return {k: v for k, v in step["payload"]["litellm_params"].items() if "cost" in k}


def _update_body(step: dict) -> dict:
    """Only the changed fields, because LiteLLM merges a PATCH and the stored params hold no secrets."""
    changes = step["changes"]
    body: dict = {}
    params: dict = {}
    if "model_name" in changes:
        body["model_name"] = changes["model_name"]["to"]
    if "litellm_model" in changes:
        params["model"] = changes["litellm_model"]["to"]
    if "credential" in changes:
        params["litellm_credential_name"] = changes["credential"]["to"]
    if "prices" in changes:
        old, new = changes["prices"]["from"], changes["prices"]["to"]
        params.update({name: value for name, value in new.items() if old.get(name) != value})
    if params:
        body["litellm_params"] = params
    if "access_groups" in changes:
        body["model_info"] = {"access_groups": changes["access_groups"]["to"]}
    return body


def _credential_values(db: Session, name: str) -> dict | str:
    """The current values of a credential, or the reason why there are none."""
    token = db.query(DBRegistryCredential.values_encrypted).filter_by(name=name).scalar()
    if not token:
        return planner.NO_VALUES.format(name)
    try:
        return decrypt_values(token)
    except (MissingKey, InvalidToken):
        return CANNOT_DECRYPT
    finally:
        db.commit()


# Values that are not secret and that an import reads from credential_info for the proxy's cloud region.
_INFO_FROM_VALUES = ("aws_region_name", "vertex_location")


def _pushed_info(db: Session, step: dict, values: dict) -> dict:
    """The credential_info that the proxy gets: the stored info, the provider, and the region fields of the values."""
    info = dict(step["payload"]["credential_info"])
    provider = db.query(DBRegistryCredential.provider).filter_by(name=step["credential"]).scalar()
    db.commit()
    if provider:
        info.setdefault("custom_llm_provider", provider)
    for name in _INFO_FROM_VALUES:
        if name in values and name not in info:
            info[name] = values[name]
    return info


def _find_created(proxy: _Proxy, step: dict, known_ids: set) -> dict | None:
    """The live deployment that a create made, or None. It reads the proxy again, also after an error answer."""
    live = read_live(proxy.url, proxy.key)
    if isinstance(live, str):
        return None
    rows, _ = without_alias_copies(live.deployments, set(live.aliases))
    payload = step["payload"]
    for row in rows:
        info = row.get("model_info") or {}
        if (
            row.get("model_name") == payload["model_name"]
            and (row.get("litellm_params") or {}).get("model") == payload["litellm_params"]["model"]
            and info.get("managed_by") == planner.MANAGED_BY
            and info.get("id") not in known_ids
        ):
            return row
    return None


def _send(proxy: _Proxy, writer: ProxyWriter, step: dict, known_ids: set, read_at: datetime) -> tuple[str, str | None]:
    """Send one step and record its result on the deployment. Returns (status, message)."""
    db, action = proxy.db, step["action"]
    if step["blocked"]:
        # A config-file deployment is never touched, so the sync skips it whatever else is wrong.
        if planner.NOT_MANAGED in step["problems"]:
            return "skipped", planner.NOT_MANAGED
        return "failed", "; ".join(step["problems"])
    if action == "update_model" and not step["changes"]:
        return "skipped", "; ".join(step["problems"]) or None

    params = (step["payload"] or {}).get("litellm_params") or {}
    secrets = [proxy.key, *_strings(params)]
    values = None
    if action in ("create_credential", "update_credential"):
        values = _credential_values(db, step["credential"])
        if isinstance(values, str):
            return "failed", values
        secrets += _strings(values)
    elif name := params.get("litellm_credential_name") or ((step["changes"] or {}).get("credential") or {}).get("to"):
        # LiteLLM puts the values of a named credential into the params, so an error can echo them too.
        if isinstance(found := _credential_values(db, name), dict):
            secrets += _strings(found)

    error = None
    try:
        if action == "create_credential":
            writer.create_credential(step["credential"], values, _pushed_info(db, step, values))
        elif action == "update_credential":
            writer.update_credential(step["credential"], values, _pushed_info(db, step, values))
        elif action == "delete_credential":
            writer.delete_credential(step["credential"])
        elif action in _CREATES:
            writer.create_model(step["payload"])
        elif action == "update_model":
            body = _update_body(step)
            secrets += _strings(body.get("litellm_params"))
            writer.update_model(step["deployment_id"], body)
        elif action == "delete_model":
            writer.delete_model(step["deployment_id"])
        elif action == "write_alias_map":
            writer.set_alias_map(step["payload"])
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        error = (exc, _error(exc, secrets))

    message = None
    if action in _CREATES:
        # LiteLLM can save the deployment and still answer with an error.
        created = _find_created(proxy, step, known_ids)
        if created is not None:
            info = created["model_info"]
            known_ids.add(info["id"])
            if error is not None:
                message = f"LiteLLM answered {short_reason(error[0])}, but the deployment exists"
            error = None
            stored = _set_row(
                db, step["row_id"], litellm_deployment_id=info["id"], model_info=without_secrets(info),
                synced_at=read_at, sync_error=None,
            )
            if not stored:
                # The registry row was deleted while the create ran, so nothing would ever delete the new one.
                try:
                    writer.delete_model(info["id"])
                    message = "the registry row was deleted while the create ran; the sync deleted the new deployment"
                except (httpx.HTTPError, httpx.InvalidURL) as exc:
                    error = (exc, "the registry row was deleted while the create ran, and the delete failed: "
                             + _error(exc, secrets))
            elif action == "replace_model":
                # The new deployment runs, so the old one can go; the model name never has none.
                try:
                    writer.delete_model(step["deployment_id"])
                except (httpx.HTTPError, httpx.InvalidURL) as exc:
                    # The row now holds the new id, so the old one gets a failed delete step of its own, which a
                    # retry takes; until then the old deployment still serves.
                    proxy.record(
                        "delete_model", "failed", _error(exc, secrets),
                        step={k: step[k] for k in ("model", "model_name", "deployment_id")},
                    )
                    message = "the new deployment runs, but the delete of the old one failed; a retry deletes it"
        elif error is None:
            error = (None, "LiteLLM answered, but the proxy shows no new deployment")

    if error is not None:
        return "failed", error[1]
    if action == "delete_model":
        # A disable keeps the row, and the id no longer names a live deployment.
        _set_row(db, step["row_id"], litellm_deployment_id=None, synced_at=read_at, sync_error=None)
    elif action == "update_model":
        _set_row(db, step["row_id"], synced_at=read_at, sync_error=None)
    return "ok", message


def _subject_steps(plan: ProxyPlan, step: dict, live: planner.LiveProxy, known_ids: frozenset) -> list[dict]:
    """The steps for the subject of one step only, planned again against the live state."""
    if step["action"] in _MODEL_STEPS:
        deployment = next(
            d for d in plan.deployments if (d.row_id, d.deployment_id) == (step["row_id"], step["deployment_id"])
        )
        # Every credential step already ran, so the live credentials are the ones to check against.
        return planner.plan(
            plan.proxy, live, [deployment], plan.fields, known_ids=known_ids,
            credential_providers=plan.credential_providers,
        )
    if step["action"] == "write_alias_map":
        return planner.plan(
            plan.proxy, live, aliases=plan.aliases, removed_alias=plan.removed_alias,
            aliases_adopted=plan.aliases_adopted,
        )
    credential = next(c for c in plan.credentials if c.name == step["credential"])
    return planner.plan(plan.proxy, live, credentials=[credential])


def _recheck(proxy: _Proxy, plan: ProxyPlan, step: dict, known_ids: set) -> tuple[dict | None, str | None]:
    """(the step to send, or None with the reason to skip it). A step that is blocked now goes back to fail."""
    live = read_live(proxy.url, proxy.key)
    if isinstance(live, str):
        return {**step, "blocked": True, "problems": [f"the proxy could not be read: {live}"]}, None
    now = _subject_steps(plan, step, live, frozenset(known_ids))
    if not now:
        return None, ALREADY_DONE
    current = now[0]
    if current["blocked"]:
        return current, None
    if any(current[key] != step[key] for key in ("action", "payload", "changes")):
        return None, STALE
    return step, None


def _sendable(step: dict) -> bool:
    return not step["blocked"] and not (step["action"] == "update_model" and not step["changes"])


def _drift(steps: list[dict]) -> list[dict]:
    """The planned steps that would send something."""
    return [
        {
            "action": step["action"], "model_name": step["model_name"], "deployment_id": step["deployment_id"],
            "fields": sorted(step["changes"] or {}),
        }
        for step in steps
        if _sendable(step)
    ]


def _unmanaged(live: planner.LiveProxy, known_ids) -> list[dict]:
    """The live deployments that no registry row holds. A repair only reports them."""
    rows, _ = without_alias_copies(live.deployments, set(live.aliases))
    return [
        {
            "model_name": row.get("model_name"),
            "litellm_model": (row.get("litellm_params") or {}).get("model"),
            "deployment_id": (row.get("model_info") or {}).get("id"),
            "db_model": (row.get("model_info") or {}).get("db_model") is True,
        }
        for row in rows
        if (row.get("model_info") or {}).get("id") not in known_ids
    ]


def _region_reason(region: DBRegion) -> str | None:
    # Rule 8 again for each region, so a run made before an env change pushes nothing.
    if not registry_manages(region.name):
        return NOT_MANAGED_BY_REGISTRY
    return unreadable(region.litellm_api_url, region.is_active is True)


def _sync_region(
    db: Session, run_id: int, region_id: int, entry: dict, wait: bool, trigger: str
) -> tuple[str, dict] | None:
    """Sync one proxy. Returns (proxy name, stats) for a full scope."""
    region = db.get(DBRegion, region_id)
    if region is None:
        logger.warning("Sync run %s: region %s no longer exists", run_id, region_id)
        return None
    proxy = _Proxy(db, run_id, region, entry, trigger)
    if reason := _region_reason(region):
        proxy.record("read_proxy", "failed", reason)
        db.commit()
        return None

    lock = _Lock(region_id)
    if not lock.acquire(wait):
        proxy.record("lock_proxy", "failed" if wait else "skipped", LOCK_BUSY if wait else LOCK_SKIPPED)
        db.commit()
        return None
    try:
        stats = _sync_locked(proxy)
    finally:
        lock.release()
    return (proxy.name, stats) if stats is not None else None


def _sync_locked(proxy: _Proxy) -> dict | None:
    db = proxy.db
    read_at = _now()
    plan, left_out = plans_for_scope(db, db.get(DBRegion, proxy.region_id), proxy.entry)
    scoped_rows = {d.row_id for d in plan.deployments if d.row_id is not None}
    by_row = {d.row_id: d for d in plan.deployments if d.row_id is not None}
    # The sync state before this run, so a drift event can tell a hand edit from a registry change.
    before = {
        row_id: (synced_at, changed_at, sync_error, prices)
        for row_id, synced_at, changed_at, sync_error, prices in db.query(
            DBRegistryModelRegion.id, DBRegistryModelRegion.synced_at, DBRegistryModelRegion.changed_at,
            DBRegistryModelRegion.sync_error, DBRegistryModelRegion.prices,
        ).filter(DBRegistryModelRegion.id.in_(scoped_rows))
    }

    def compared_price(row_id):
        # Only a compared price that the sync writes is known to be on the proxy; a removed deployment has none.
        found = by_row.get(row_id)
        if found is None or not found.keep or "prices" not in plan.fields or found.provisioned_only or found.price is None:
            return None
        return found.price
    # No transaction stays open during HTTP.
    db.commit()

    live = read_live(proxy.url, proxy.key)
    if isinstance(live, str):
        # ponytail: an empty LiteLLM proxy answers /model/info with HTTP 500, so the sync cannot plan for it.
        # Seed one deployment by hand, or read an empty proxy as empty if LiteLLM keeps that answer.
        proxy.record("read_proxy", "failed", f"the proxy could not be read: {live}")
        db.commit()
        return None
    # An empty wanted alias map with live aliases is blocked in the planner (DELETES_EVERY_ALIAS), so only the
    # alias step fails and the other steps of the proxy still run.
    known_ids = set(plan.known_ids)
    planned = plan_proxy(plan, live)
    # An update with only notes sends nothing, so the run records no step for it; a dry run still shows it.
    steps = [step for step in planned if _sendable(step) or step["blocked"]]
    stats = None
    if proxy.entry["full"]:
        stats = {
            "drift": _drift(steps), "unmanaged": _unmanaged(live, known_ids), "failed": left_out,
            # Cost fields that LiteLLM keeps and the sync cannot replace, per deployment row id.
            "stale_costs": {str(s["row_id"]): s["stale_cost_fields"] for s in planned if s.get("stale_cost_fields")},
        }
    ids = [proxy.record(step["action"], "pending", step=step) for step in steps]
    stepped = {step["row_id"] for step in steps}
    # A deployment in the scope with no step is already in sync.
    removed = frozenset(d.row_id for d in plan.deployments if d.row_id is not None and not d.keep)
    for row_id in scoped_rows - stepped:
        _mark_in_sync(db, row_id, read_at, removed, compared_price(row_id))
    repair = proxy.trigger in ("daily", "repair")
    if repair:
        # Drift that no write pushed is a change too, so changed_at is never later than synced_at after a success.
        for step in steps:
            if _sendable(step):
                _set_row(db, step["row_id"], changed_at=read_at)
    db.commit()

    with ProxyWriter(proxy.url, proxy.key) as writer:
        # ponytail: one live read per step; batch the reads if a proxy gets slow.
        for index, (step_id, step) in enumerate(zip(ids, steps, strict=True)):
            # The first step goes out on the read above; each later step checks the proxy again.
            current, reason = _recheck(proxy, plan, step, known_ids) if index and _sendable(step) else (step, None)
            if current is None:
                status, message = "skipped", reason
            else:
                status, message = _send(proxy, writer, current, known_ids, read_at)
            if status == "ok" and current["problems"]:
                # The problems of a step that was sent are notes; the run detail shows them as a dry run does.
                message = "; ".join(filter(None, [message, *current["problems"]]))
            proxy.finish(step_id, step["action"], status, message)
            # A step skipped as done, or an update with nothing to send, leaves the deployment in sync.
            in_sync = status == "skipped" and (reason == ALREADY_DONE or (current is step and not step["blocked"]))
            if status == "failed":
                _set_row(db, step["row_id"], sync_error=message)
            elif in_sync:
                _mark_in_sync(db, step["row_id"], read_at, removed, compared_price(step["row_id"]))
            elif status == "ok" and step["action"] in _MODEL_STEPS:
                # The same price as _mark_in_sync stores; a write run that compared no price keeps what a create sent.
                price = compared_price(step["row_id"])
                if price is None:
                    price = _sent_price(step)
                if price is not None:
                    _set_row(db, step["row_id"], **_price_columns(price))
                if repair and _hand_edit(before.get(step["row_id"]), by_row.get(step["row_id"]), step):
                    _record_repair(proxy, step)
            db.commit()
    return stats


def execute_run(run_id: int, wait: bool = True) -> None:
    """Run a pending sync run. The entry point of BackgroundTasks and the daily step; it opens its own session."""
    db = SessionLocal()
    try:
        started = (
            db.query(DBRegistrySyncRun)
            .filter_by(id=run_id, status="pending")
            .update({"status": "running", "started_at": _now()}, synchronize_session=False)
        )
        db.commit()
        if not started:
            return
        run = db.get(DBRegistrySyncRun, run_id)
        scope, trigger = dict(run.scope), run.trigger
        db.commit()
        error, proxies = None, {}
        try:
            for region_id in sorted(int(key) for key in scope):
                if found := _sync_region(db, run_id, region_id, scope[str(region_id)], wait, trigger):
                    proxies[found[0]] = found[1]
        except Exception as exc:
            db.rollback()
            logger.exception("Sync run %s stopped", run_id)
            error = type(exc).__name__
        failed = error is not None or db.query(DBRegistrySyncStep.id).filter_by(run_id=run_id, status="failed").first()
        values = {"status": "failed" if failed else "ok", "finished_at": _now(), "error": error}
        if proxies:
            values["stats"] = {"proxies": proxies}
        db.query(DBRegistrySyncRun).filter_by(id=run_id).update(values, synchronize_session=False)
        db.commit()
    finally:
        db.close()


STUCK = "the run did not finish; the process that ran it stopped"


def run_registry_sync(db: Session) -> dict:
    """The daily step: close stuck runs, adopt-import new proxies, then repair each registry-managed proxy."""
    return record_run(db, "registry-sync", lambda: _daily(db))


def _daily(db: Session) -> dict:
    now = _now()
    # BackgroundTasks has no retry, so a run whose process stopped stays open until this marks it.
    stuck = (
        db.query(DBRegistrySyncRun)
        .filter(
            DBRegistrySyncRun.status.in_(("pending", "running")),
            func.coalesce(DBRegistrySyncRun.started_at, DBRegistrySyncRun.created_at)
            < now - timedelta(minutes=config.SYNC_STUCK_MINUTES),
        )
        .update({"status": "failed", "error": STUCK, "finished_at": now}, synchronize_session=False)
    )
    db.commit()
    stats: dict = {"stuck": stuck, "imported": [], "import_failed": {}, "runs": {}}

    # A plain import read no aliases, so a proxy is done only when the adopt import ran.
    adopted = {rid for (rid,) in db.query(DBRegistryProxy.region_id).filter(DBRegistryProxy.adopted_at.isnot(None))}
    regions = [(region, region.id, region.name) for region in active_regions(db)]
    for region, region_id, name in regions:
        if region_id in adopted:
            continue
        try:
            import_proxy(db, region, adopt=True)
            stats["imported"].append(name)
            adopted.add(region_id)
        except Exception as exc:
            # One proxy that cannot be read must not stop the repair of the others.
            stats["import_failed"][name] = short_reason(exc)

    managed = [(region_id, name) for _, region_id, name in regions if region_id in adopted and registry_manages(name)]
    stats["managed"] = [name for _, name in managed]
    for region_id, name in managed:
        run = create_run(db, "daily", full_scope([region_id], skip_failed=True))
        db.commit()
        run_id = run.id
        # A busy proxy is skipped with a note; the next daily run repairs it.
        execute_run(run_id, wait=False)
        stats["runs"][name] = run_id
    return stats
