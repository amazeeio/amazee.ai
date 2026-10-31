"""The sync engine: push the registry state of a sync run to its proxies.

It imports the planning helpers of dry_run.py and never a route module, so a script can import it with no
import cycle. A run saves only ids in its scope and reads the wanted state again after it takes the proxy lock.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import text
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
    read_live,
)
from app.registry.config import registry_manages
from app.registry.crypto import MissingKey, decrypt_values
from app.registry.litellm import ProxyWriter, without_alias_copies, without_secrets
from app.registry.models import (
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModelRegion,
    DBRegistrySyncRun,
    DBRegistrySyncStep,
)

logger = logging.getLogger(__name__)

ALREADY_DONE = "the proxy already has this state"
STALE = "the proxy changed after the plan; the next repair plans it again"
LOCK_BUSY = "another sync run holds the proxy"
LOCK_SKIPPED = "another sync run holds the proxy; the next run repairs it"
NOT_MANAGED_BY_REGISTRY = "the registry does not manage the proxy"
CANNOT_DECRYPT = "the credential values cannot be decrypted with the keys in REGISTRY_CREDENTIAL_KEYS"
_LOCK_TIMEOUT = "55P03"
_MODEL_STEPS = ("create_model", "update_model", "delete_model")


def _now() -> datetime:
    return datetime.now(UTC)


def _entry() -> dict:
    return {
        "deployments": [], "deleted": [], "fields": [], "aliases": False, "removed_alias": None,
        "credentials": [], "full": False, "skip_failed": False,
    }


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
            {"name": name, "rotate": False, "keep": True}
            for (name,) in db.query(DBRegistryCredential.name)
            .join(DBRegistryCredentialRegion, DBRegistryCredentialRegion.credential_id == DBRegistryCredential.id)
            .filter(DBRegistryCredentialRegion.region_id == region.id)
            .order_by(DBRegistryCredential.name)
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
        plan = replace(plan, aliases=alias_plans(db, region)[0].aliases, removed_alias=entry.get("removed_alias"))
    if entry["credentials"]:
        plan = replace(plan, credentials=tuple(
            credential_plans(db, item["name"], [region.id], item["rotate"], item["keep"])[0].credentials[0]
            for item in entry["credentials"]
        ))
    return plan, left_out


def create_run(db: Session, trigger: str, plans_or_scope) -> DBRegistrySyncRun:
    """Add a pending run. The caller commits it with its write, so the run exists before its task starts."""
    scope = plans_or_scope if isinstance(plans_or_scope, dict) else scope_of(plans_or_scope)
    run = DBRegistrySyncRun(
        trigger=trigger, status="pending", region_ids=sorted(int(region_id) for region_id in scope), scope=scope
    )
    db.add(run)
    db.flush()
    return run


def _scrub(message: str, secrets) -> str:
    """The message with each secret of 8 characters or more replaced, because LiteLLM can echo a payload."""
    for secret in sorted({s for s in secrets if isinstance(s, str) and len(s) >= 8}, key=len, reverse=True):
        message = message.replace(secret, "********")
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

    def __init__(self, db: Session, run_id: int, region: DBRegion, entry: dict):
        self.db, self.run_id, self.entry = db, run_id, entry
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
            writer.create_credential(step["credential"], values, step["payload"]["credential_info"])
        elif action == "update_credential":
            writer.update_credential(step["credential"], values, step["payload"]["credential_info"])
        elif action == "delete_credential":
            writer.delete_credential(step["credential"])
        elif action == "create_model":
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
    if action == "create_model":
        # LiteLLM can save the deployment and still answer with an error.
        created = _find_created(proxy, step, known_ids)
        if created is not None:
            info = created["model_info"]
            known_ids.add(info["id"])
            if error is not None:
                answer = (
                    f"HTTP {error[0].response.status_code}"
                    if isinstance(error[0], httpx.HTTPStatusError) else type(error[0]).__name__
                )
                message = f"LiteLLM answered {answer}, but the deployment exists"
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
        return planner.plan(plan.proxy, live, aliases=plan.aliases, removed_alias=plan.removed_alias)
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


def _drift(steps: list[dict]) -> list[dict]:
    """The planned steps that would send something."""
    return [
        {
            "action": step["action"], "model_name": step["model_name"], "deployment_id": step["deployment_id"],
            "fields": sorted(step["changes"] or {}),
        }
        for step in steps
        if not step["blocked"] and not (step["action"] == "update_model" and not step["changes"])
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


def _region_reason(region: DBRegion | None) -> str | None:
    if region is None:
        return "the proxy no longer exists"
    # Rule 8 again for each region, so a run made before an env change pushes nothing.
    if not registry_manages(region.name):
        return NOT_MANAGED_BY_REGISTRY
    if region.is_active is not True:
        return "the proxy is inactive"
    if not region.litellm_api_url:
        return "no proxy URL"
    return None


def _sync_region(db: Session, run_id: int, region_id: int, entry: dict, wait: bool) -> tuple[str, dict] | None:
    """Sync one proxy. Returns (proxy name, stats) for a full scope."""
    region = db.get(DBRegion, region_id)
    if region is None:
        logger.warning("Sync run %s: region %s no longer exists", run_id, region_id)
        return None
    proxy = _Proxy(db, run_id, region, entry)
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
    # No transaction stays open during HTTP.
    db.commit()

    live = read_live(proxy.url, proxy.key)
    if isinstance(live, str):
        # ponytail: an empty LiteLLM proxy answers /model/info with HTTP 500, so the sync cannot plan for it.
        # Seed one deployment by hand, or read an empty proxy as empty if LiteLLM keeps that answer.
        proxy.record("read_proxy", "failed", f"the proxy could not be read: {live}")
        db.commit()
        return None
    if plan.aliases is not None and not plan.aliases and live.aliases.keys() - {plan.removed_alias}:
        # The registry never wipes live aliases that the adopt import has not read.
        proxy.record("write_alias_map", "failed", planner.DELETES_EVERY_ALIAS)
        db.commit()
        return None

    known_ids = set(plan.known_ids)
    steps = plan_proxy(plan, live, frozenset(known_ids))
    stats = None
    if proxy.entry["full"]:
        stats = {"drift": _drift(steps), "unmanaged": _unmanaged(live, known_ids), "failed": left_out}
    ids = [proxy.record(step["action"], "pending", step=step) for step in steps]
    stepped = {step["row_id"] for step in steps}
    # A deployment in the scope with no step is already in sync.
    for row_id in scoped_rows - stepped:
        _set_row(db, row_id, synced_at=read_at, sync_error=None)
    db.commit()

    with ProxyWriter(proxy.url, proxy.key) as writer:
        # ponytail: one live read per step; batch the reads if a proxy gets slow.
        for index, (step_id, step) in enumerate(zip(ids, steps, strict=True)):
            sendable = not step["blocked"] and not (step["action"] == "update_model" and not step["changes"])
            # The first step goes out on the read above; each later step checks the proxy again (AC 12).
            current, reason = _recheck(proxy, plan, step, known_ids) if index and sendable else (step, None)
            if current is None:
                status, message = "skipped", reason
            else:
                status, message = _send(proxy, writer, current, known_ids, read_at)
            proxy.finish(step_id, step["action"], status, message)
            # A step skipped as done, or an update with nothing to send, leaves the deployment in sync.
            in_sync = status == "skipped" and (reason == ALREADY_DONE or (current is step and not step["blocked"]))
            if status == "failed":
                _set_row(db, step["row_id"], sync_error=message)
            elif in_sync:
                _set_row(db, step["row_id"], synced_at=read_at, sync_error=None)
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
        scope = dict(run.scope)
        db.commit()
        error, proxies = None, {}
        try:
            for region_id in sorted(int(key) for key in scope):
                if found := _sync_region(db, run_id, region_id, scope[str(region_id)], wait):
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
