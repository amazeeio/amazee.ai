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
        
        # LiteLLM keys /model/update and /model/delete on the deployment id
        # (model_info.id), and /model/new allows duplicate model_names — so
        # resolve existing deployment ids first and upsert accordingly, instead
        # of add-then-catch-conflict (which would silently create duplicates).
        deployment_ids = await litellm_service.get_model_deployment_ids(model.model_id)

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
            alias_map = region_model_group_alias_map(db, region_id)
            # A DB row saying the target is deployed is not enough — its own
            # sync may still be pending or failed. Only route to model groups
            # that actually exist on the proxy, or the alias would 404.
            info = await litellm_service.get_model_info()
            live_names = {
                entry.get("model_name")
                for entry in (info.get("data") or [])
                if isinstance(entry, dict)
            }
            alias_map = {a: t for a, t in alias_map.items() if t in live_names}
            # Write the map before deleting any legacy alias-as-model entry:
            # if the delete then fails, both mechanisms briefly coexist (which
            # still serves requests) and the retry cleans up — the reverse
            # order would leave the alias with neither.
            await litellm_service.set_model_group_aliases(alias_map)
            if deployment_ids:
                logger.info(f"Removing legacy alias model entry '{model.model_id}' from region '{region.name}'")
                await litellm_service.delete_model(model.model_id, deployment_ids)
            if (
                assoc.is_active
                and model.is_active_globally
                and model.model_id not in alias_map
            ):
                assoc.sync_status = "failed"
                assoc.sync_error = (
                    f"Alias '{model.model_id}' has no active target deployment "
                    "in this region (or its target has not synced yet)."
                )
                db.commit()
                logger.error(f"Sync failed for model_id={model_id}, region_id={region_id}: {assoc.sync_error}")
                return
        elif assoc.is_active and model.is_active_globally:
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
            if deployment_ids:
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

