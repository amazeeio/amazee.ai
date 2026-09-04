import logging
import traceback
from datetime import datetime, UTC

from sqlalchemy import text

from app.core.config import catalog_manages
from app.db.database import get_db
from app.db.models import DBModel, DBModelAliasTarget, DBModelRegion, DBRegion
from app.services.access_groups import (
    model_access_group_slugs,
    sync_region_access_group_entities,
)
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

# First key of the two-int postgres advisory lock serializing per-region
# writes of router_settings.model_group_alias (second key: region id).
ALIAS_MAP_LOCK_NAMESPACE = 743_291_648


def effective_litellm_params(
    db, model: DBModel, region_id: int
) -> tuple[dict | None, str | None]:
    """Resolve the params actually pushed to a region: base litellm_params
    merged with the region's override — or, for an alias, the region target's
    effective params (pointer semantics). Returns (params, error)."""
    if model.is_alias:
        target_row = (
            db.query(DBModelAliasTarget)
            .filter_by(alias_model_id=model.id, region_id=region_id)
            .first()
        )
        if not target_row:
            return None, f"Alias '{model.model_id}' has no target model for this region."
        target = db.query(DBModel).filter_by(id=target_row.target_model_id).first()
        if not target or target.deleted_at is not None or not target.is_active_globally:
            return None, f"Alias '{model.model_id}' target is missing or inactive."
        if target.is_alias:
            return None, f"Alias '{model.model_id}' points at another alias; chains are not supported."
        params, error = effective_litellm_params(db, target, region_id)
        if error:
            return None, error
        # The deployment is registered under the ALIAS name; make sure the
        # backend `model` stays the target's (add_model would otherwise
        # default it to the alias name).
        if "model" not in params:
            params["model"] = target.model_id
        return params, None

    assoc = db.query(DBModelRegion).filter_by(model_id=model.id, region_id=region_id).first()
    override = dict(assoc.litellm_params_override or {}) if assoc else {}
    return {**dict(model.litellm_params or {}), **override}, None


# /model/info strips credentials and masks any key whose name segment reads
# like a secret, so those keys can never be compared against the catalog.
_PROXY_STRIPPED_PARAM_KEYS = {
    "api_key",
    "client_secret",
    "vertex_credentials",
    "vertex_ai_credentials",
    "aws_access_key_id",
    "aws_secret_access_key",
}
_PROXY_MASKED_SEGMENTS = {"password", "secret", "key", "token"}


def _is_masked_param(key: str) -> bool:
    segments = set(key.lower().replace("-", "_").split("_"))
    return bool(segments & _PROXY_MASKED_SEGMENTS) and "cost" not in segments


def _live_params(deployments: list[dict]) -> dict | None:
    """The union of the deployments' litellm_params, or None when the entries
    carry none at all (nothing to compare against)."""
    with_params = [e for e in deployments if isinstance(e.get("litellm_params"), dict)]
    if not with_params:
        return None
    merged: dict = {}
    for entry in with_params:
        merged.update(entry["litellm_params"])
    return merged


def _sent_params(desired: dict) -> dict:
    # LiteLLM never stores a None value, so it can never be seen live.
    return {k: v for k, v in desired.items() if v is not None}


def stale_param_keys(deployments: list[dict], desired: dict) -> set[str]:
    """Keys live on the proxy that the catalog no longer sends. /model/update
    merges, so these survive every update until the deployment is recreated."""
    live = _live_params(deployments)
    if live is None:
        return set()
    # LiteLLM stores its own boolean flags (GenericLiteLLMParams defaults such
    # as use_in_pass_through) on every deployment as False, and the set grows
    # with each release. Treat any unsent False as LiteLLM's own; the cost is
    # that a dropped catalog key whose value was false is left in place, which
    # is what LiteLLM would default it to anyway.
    injected = {k for k, v in live.items() if v is False}
    return set(live) - set(_sent_params(desired)) - {"model"} - injected


def _value_comparable(key: str, value) -> bool:
    # Masked values, nested dicts (masked per sub-key) and os.environ/ refs
    # (resolved before /model/info reports them) never match the catalog.
    if _is_masked_param(key) or isinstance(value, dict):
        return False
    return not (isinstance(value, str) and value.startswith("os.environ/"))


def params_drifted(deployments: list[dict], desired: dict) -> bool:
    """True when the proxy's params differ from the catalog's for a comparable
    key: a stale key, a missing key, or a changed non-secret value."""
    if _live_params(deployments) is None:
        return False
    stale = stale_param_keys(deployments, desired)
    if stale:
        logger.info(f"Params drift: stale keys {sorted(stale)} on the proxy")
        return True
    # Every deployment under the name must match; a duplicate that kept an
    # old value is drift too.
    for entry in deployments:
        live = entry.get("litellm_params")
        if not isinstance(live, dict):
            continue
        for key, value in _sent_params(desired).items():
            if key in _PROXY_STRIPPED_PARAM_KEYS:
                continue
            if key not in live:
                logger.info(f"Params drift: '{key}' missing on the proxy")
                return True
            if _value_comparable(key, value) and live[key] != value:
                logger.info(f"Params drift: '{key}' differs on the proxy")
                return True
    return False


def region_model_group_alias_map(db, region_id: int) -> dict[str, str]:
    """The full desired ``model_group_alias`` map for one region's proxy:
    every active alias with an active target deployment in that region.
    Router-level state — always computed whole, never per-alias deltas."""
    alias_map: dict[str, str] = {}
    rows = (
        db.query(DBModel, DBModelAliasTarget)
        .join(DBModelAliasTarget, DBModelAliasTarget.alias_model_id == DBModel.id)
        .join(
            DBModelRegion,
            (DBModelRegion.model_id == DBModel.id)
            & (DBModelRegion.region_id == region_id)
            & (DBModelRegion.is_active.is_(True)),
        )
        .filter(
            DBModelAliasTarget.region_id == region_id,
            DBModel.is_alias.is_(True),
            DBModel.is_active_globally.is_(True),
            DBModel.deleted_at.is_(None),
        )
        .all()
    )
    for alias, target_row in rows:
        target = db.query(DBModel).filter_by(id=target_row.target_model_id).first()
        if not target or target.deleted_at is not None or not target.is_active_globally:
            continue
        deployed = (
            db.query(DBModelRegion)
            .filter_by(model_id=target.id, region_id=region_id, is_active=True)
            .first()
        )
        if deployed:
            alias_map[alias.model_id] = target.model_id
    return alias_map


def _extract_alias_map(router_settings: dict) -> dict[str, str]:
    """Normalize /router/settings' model_group_alias field to {alias: target}.
    Values may be plain strings or {'model': ..., 'hidden': ...} dicts."""
    for field in router_settings.get("fields") or []:
        if isinstance(field, dict) and field.get("field_name") == "model_group_alias":
            value = field.get("field_value")
            if not isinstance(value, dict):
                return {}
            return {
                str(alias): str(target.get("model")) if isinstance(target, dict) else str(target)
                for alias, target in value.items()
            }
    return {}


async def reconcile_region_models(db, region: DBRegion) -> dict:
    """Detect and repair drift between the DB's desired state and the region's
    proxy. 'synced' is a claim about the past: models can vanish behind our
    back (proxy admin UI, DB restores, cluster rollouts). This compares the
    proxy's live models and alias map against what the DB says should exist
    and re-runs the ordinary sync task for anything drifted — so the region
    continuously converges to the catalog.
    """
    service = LiteLLMService(api_url=region.litellm_api_url, api_key=region.litellm_api_key)
    info = await service.get_model_info()
    entries = [e for e in (info.get("data") or []) if isinstance(e, dict)]
    all_names = {e.get("model_name") for e in entries}
    # Only DB-registered entries are ours to delete; a same-named config-file
    # model still satisfies "the model is available".
    db_names = {
        e.get("model_name") for e in entries if (e.get("model_info") or {}).get("db_model")
    }

    to_sync: list[int] = []
    rows = (
        db.query(DBModelRegion, DBModel)
        .join(DBModel, DBModel.id == DBModelRegion.model_id)
        .filter(DBModelRegion.region_id == region.id, DBModel.is_alias.is_(False))
        .all()
    )
    for assoc, model in rows:
        desired = bool(
            assoc.is_active and model.is_active_globally and model.deleted_at is None
        )
        drifted = (desired and model.model_id not in all_names) or (
            not desired and model.model_id in db_names
        )
        if desired and not drifted and model.model_id in db_names:
            params, error = effective_litellm_params(db, model, region.id)
            deployments = [
                e
                for e in entries
                if e.get("model_name") == model.model_id
                and (e.get("model_info") or {}).get("db_model")
            ]
            drifted = error is None and params_drifted(deployments, params)
        if drifted:
            assoc.sync_status = "pending"
            assoc.updated_at = datetime.now(UTC)
            to_sync.append(model.id)

    # Any single alias sync rewrites the region's whole map, so one drifted
    # map costs one sync of whichever alias row exists.
    alias_pk: int | None = None
    desired_map = region_model_group_alias_map(db, region.id)
    actual_map = _extract_alias_map(await service.get_router_settings())
    if desired_map != actual_map:
        alias_row = (
            db.query(DBModelRegion)
            .join(DBModel, DBModel.id == DBModelRegion.model_id)
            .filter(DBModelRegion.region_id == region.id, DBModel.is_alias.is_(True))
            .first()
        )
        if alias_row:
            alias_row.sync_status = "pending"
            alias_row.updated_at = datetime.now(UTC)
            alias_pk = alias_row.model_id
    db.commit()

    for model_pk in to_sync:
        await sync_model_to_region_task(model_pk, region.id)
    if alias_pk is not None:
        await sync_model_to_region_task(alias_pk, region.id)
    if to_sync or alias_pk is not None:
        logger.warning(
            f"Reconcile repaired drift on region {region.name}: "
            f"{len(to_sync)} model(s) resynced, alias map rewritten: {alias_pk is not None}"
        )
    return {"models_resynced": len(to_sync), "alias_map_rewritten": alias_pk is not None}


async def reconcile_all_managed_regions(db) -> dict[str, dict]:
    """Run drift reconciliation for every active catalog-managed region.
    Regions are independent — one unreachable proxy must not stop the rest."""
    results: dict[str, dict] = {}
    for region in db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all():
        if not catalog_manages(region.name):
            continue
        try:
            results[region.name] = await reconcile_region_models(db, region)
        except Exception as e:
            logger.error(f"Reconcile failed for region {region.name}: {e}")
            results[region.name] = {"error": str(e)}
    return results


def _scrub_secrets(text: str, litellm_params) -> str:
    """Proxy error bodies can echo the request payload (e.g. a 422 quoting
    litellm_params, api_key included). sync_error is shown in admin responses,
    so blank out any stored string param value that appears in the text."""
    if not litellm_params:
        return text
    for v in litellm_params.values():
        if isinstance(v, dict):
            text = _scrub_secrets(text, v)
        elif isinstance(v, str) and len(v) >= 8 and not v.startswith("os.environ/"):
            text = text.replace(v, "********")
    return text


async def sync_model_to_region_task(model_id: int, region_id: int) -> None:
    """
    Background task to synchronize a model's state to a regional LiteLLM proxy.
    Creates its own DB session to avoid lifecycle issues with request-scoped DB sessions.
    Updates the DBModelRegion status to 'synced' or 'failed' based on the result.
    """
    logger.info(f"Starting background synchronization of model_id={model_id} to region_id={region_id}")
    
    db = None
    assoc = None
    model = None
    pushed_params = None
    try:
        db = next(get_db())
        # Fetch model_region association
        assoc = db.query(DBModelRegion).filter_by(model_id=model_id, region_id=region_id).first()
        if not assoc:
            logger.error(f"Sync failed: DBModelRegion association not found for model_id={model_id}, region_id={region_id}")
            return
            
        # Fetch model and region
        model = db.query(DBModel).filter_by(id=model_id).first()
        region = db.query(DBRegion).filter_by(id=region_id).first()
        
        if not model or not region:
            logger.error(f"Sync failed: Model or Region not found for model_id={model_id}, region_id={region_id}")
            assoc.sync_status = "failed"
            assoc.sync_error = f"Model found: {bool(model)}, Region found: {bool(region)}"
            assoc.updated_at = datetime.now(UTC)
            db.commit()
            return

        # Check region active status
        if not region.is_active:
            logger.warning(f"Sync skipped: Region {region.name} is inactive.")
            assoc.sync_status = "failed"
            assoc.sync_error = f"Region {region.name} is inactive and cannot be synchronized."
            assoc.updated_at = datetime.now(UTC)
            db.commit()
            return

        # Last line of defence before any write to a proxy: a region the
        # catalog does not manage (private regions like ren2, or every region
        # while the catalog is still gated off) is never touched.
        if not catalog_manages(region.name):
            logger.warning(f"Sync skipped: region {region.name} is not catalog-managed.")
            assoc.sync_status = "not_configured"
            assoc.sync_error = f"Region {region.name} is not catalog-managed (CATALOG_MANAGED_REGIONS)."
            assoc.updated_at = datetime.now(UTC)
            db.commit()
            return

        # Instantiate LiteLLM client for the region
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key
        )
        
        if model.is_alias:
            # Real aliases live in router_settings.model_group_alias, not as
            # duplicate model entries (auth resolves the alias to its target,
            # so access-group checks apply to the target's tags). The map is
            # one router-wide dict, so activation, retargeting and
            # deactivation are all "recompute and rewrite the full map".
            #
            # Serialize map writes per region: concurrent alias syncs (e.g.
            # overlapping catalog applies across uvicorn workers) would
            # otherwise race on the shared dict and the last — possibly
            # stalest — writer would win. The advisory lock is transaction-
            # scoped, so compute-and-push always runs against DB state at
            # least as fresh as any earlier writer's.
            db.execute(
                text("SELECT pg_advisory_xact_lock(:ns, :region)"),
                {"ns": ALIAS_MAP_LOCK_NAMESPACE, "region": region_id},
            )
            # The map is computed from DB state ONLY — deliberately not
            # filtered against the proxy's live /model/info. During a rollout
            # the proxy's model list is mid-churn (replica lag, router
            # reloads), and a stale read here would bake missing aliases into
            # the map with sync_status happily 'synced' (observed on us1: the
            # last writer's stale snapshot shrank the map to one entry). A
            # target whose own sync has not landed yet merely 404s until it
            # does — the name-based mapping then works with no alias rewrite.
            alias_map = region_model_group_alias_map(db, region_id)
            # Write the map before deleting any legacy alias-as-model entry:
            # if the delete then fails, both mechanisms briefly coexist (which
            # still serves requests) and the retry cleans up — the reverse
            # order would leave the alias with neither.
            await litellm_service.set_model_group_aliases(alias_map)
            # Legacy cleanup, guarded: once the map is live, LiteLLM expands
            # every alias into a synthetic /model/info entry identical to its
            # target — INCLUDING the target's deployment id. Deleting "ids
            # under the alias name" therefore deletes the target itself
            # (observed on de1: claude-4-5-sonnet vanished). A real legacy
            # entry has its own id that appears under no other model_name, so
            # only ids exclusive to the alias name are legacy and deletable.
            info = await litellm_service.get_model_info()
            ids_by_name: dict[str, set[str]] = {}
            for entry in info.get("data") or []:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("model_info"), dict)
                    and entry["model_info"].get("id")
                ):
                    ids_by_name.setdefault(entry.get("model_name"), set()).add(
                        entry["model_info"]["id"]
                    )
            other_ids: set[str] = set()
            for name, ids in ids_by_name.items():
                if name != model.model_id:
                    other_ids |= ids
            legacy_ids = ids_by_name.get(model.model_id, set()) - other_ids
            if legacy_ids:
                logger.info(f"Removing legacy alias model entry '{model.model_id}' from region '{region.name}'")
                await litellm_service.delete_model(model.model_id, sorted(legacy_ids))
            if (
                assoc.is_active
                and model.is_active_globally
                and model.model_id not in alias_map
            ):
                assoc.sync_status = "failed"
                assoc.sync_error = (
                    f"Alias '{model.model_id}' has no active target deployment in this region."
                )
                db.commit()
                logger.error(f"Sync failed for model_id={model_id}, region_id={region_id}: {assoc.sync_error}")
                return
        elif assoc.is_active and model.is_active_globally:
            # LiteLLM keys /model/update and /model/delete on the deployment id
            # (model_info.id), and /model/new allows duplicate model_names — so
            # resolve existing deployment ids first and upsert accordingly,
            # instead of add-then-catch-conflict (which would silently create
            # duplicates).
            deployments = await litellm_service.get_model_deployments(model.model_id)
            deployment_ids = [entry["model_info"]["id"] for entry in deployments]
            # Only DB-registered deployments are ours to compare and replace; a
            # same-named config-file entry would otherwise look stale forever.
            db_deployments = [d for d in deployments if d["model_info"].get("db_model")]
            # Base params + per-region override, or the alias target's params.
            params, resolve_error = effective_litellm_params(db, model, region_id)
            pushed_params = params
            if resolve_error:
                assoc.sync_status = "failed"
                assoc.sync_error = resolve_error
                db.commit()
                logger.error(f"Sync failed for model_id={model_id}, region_id={region_id}: {resolve_error}")
                return
            # Access-group tags are derived state: groups containing this model
            # that are deployed to this region. Always sent (possibly []) so
            # removing a model from its last group clears the tags on the proxy.
            access_groups = model_access_group_slugs(db, model.id, region_id)
            stale_keys = stale_param_keys(db_deployments, params)
            if stale_keys:
                # /model/update merges into the stored params, so a key the
                # catalog dropped can only go away by recreating the
                # deployment. Register the replacement before deleting the
                # old ids so the model name is never unavailable.
                logger.info(
                    f"Recreating model '{model.model_id}' in region '{region.name}' "
                    f"to drop stale params {sorted(stale_keys)} (deployments: {deployment_ids})"
                )
                await litellm_service.add_model(
                    model.model_id, params, access_groups=access_groups
                )
                await litellm_service.delete_model(
                    model.model_id, [d["model_info"]["id"] for d in db_deployments]
                )
            elif deployment_ids:
                logger.info(f"Updating model '{model.model_id}' in region '{region.name}' (deployments: {deployment_ids})")
                await litellm_service.update_model(
                    model.model_id, params, deployment_ids, access_groups=access_groups
                )
            else:
                logger.info(f"Registering model '{model.model_id}' in region '{region.name}'")
                await litellm_service.add_model(
                    model.model_id, params, access_groups=access_groups
                )
        else:
            logger.info(f"Deregistering model '{model.model_id}' from region '{region.name}'")
            deployment_ids = await litellm_service.get_model_deployment_ids(model.model_id)
            await litellm_service.delete_model(model.model_id, deployment_ids)

        # A model coming or going changes group membership, so mirror the
        # region's access groups into the proxy's entity registry (the list
        # the LiteLLM admin UI shows). Aliases are never entity members.
        if not model.is_alias:
            await sync_region_access_group_entities(db, region, service=litellm_service)

        # Success!
        assoc.sync_status = "synced"
        assoc.sync_error = None
        assoc.synced_at = datetime.now(UTC)
        logger.info(f"Successfully synchronized model_id={model_id} to region_id={region_id}")
        
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Sync failed for model_id={model_id}, region_id={region_id}: {e}\n{error_trace}")
        # Refresh assoc from DB to avoid session binding issues if any
        if db:
            try:
                assoc = db.query(DBModelRegion).filter_by(model_id=model_id, region_id=region_id).first()
                if assoc:
                    assoc.sync_status = "failed"
                    # Scrub every params source the request could have echoed:
                    # base params, the per-region override, and the effective
                    # params actually pushed (an alias pushes its TARGET's).
                    scrubbed = _scrub_secrets(str(e), model.litellm_params if model else None)
                    scrubbed = _scrub_secrets(scrubbed, assoc.litellm_params_override)
                    assoc.sync_error = _scrub_secrets(scrubbed, pushed_params)
            except Exception as inner_e:
                logger.error(f"Failed to write error status to DB: {inner_e}")
                # Session is poisoned — drop assoc so finally can't commit the
                # in-memory 'synced' state for a sync that actually failed.
                assoc = None
                try:
                    db.rollback()
                except Exception:
                    # Best-effort rollback during error handling; ignore rollback
                    # failures here and let finally close the session.
                    pass
        
    finally:
        if db:
            if assoc:
                assoc.updated_at = datetime.now(UTC)
                db.commit()
            db.close()

