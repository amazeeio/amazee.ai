from fastapi import HTTPException
import logging
import traceback
from datetime import datetime, UTC

from app.db.database import get_db
from app.db.models import DBModel, DBModelRegion, DBRegion
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)


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

        # Instantiate LiteLLM client for the region
        litellm_service = LiteLLMService(
            api_url=region.litellm_api_url,
            api_key=region.litellm_api_key
        )
        
        if assoc.is_active and model.is_active_globally:
            logger.info(f"Registering/Updating model '{model.model_id}' in region '{region.name}'")
            # Try to add model first
            try:
                await litellm_service.add_model(model.model_id, model.litellm_params)
            except Exception as add_err:
                # Fallback to update model only if the error explicitly indicates
                # that the model is already registered / already exists
                # add_model preserves LiteLLM's 4xx status (409 already-exists);
                # the string match is a fallback for versions returning 400.
                is_already_exists = False
                if isinstance(add_err, HTTPException):
                    is_already_exists = (
                        add_err.status_code == 409 or
                        (add_err.detail and any(marker in str(add_err.detail).lower() for marker in ["already", "exists", "conflict"]))
                    )
                else:
                    err_str = str(add_err).lower()
                    is_already_exists = any(marker in err_str for marker in ["already", "exists", "conflict"])
                
                if is_already_exists:
                    logger.warning(f"LiteLLM add_model for '{model.model_id}' failed with existing registration indicator: {add_err}. Retrying with update_model.")
                    await litellm_service.update_model(model.model_id, model.litellm_params)
                else:
                    raise add_err
        else:
            logger.info(f"Deregistering model '{model.model_id}' from region '{region.name}'")
            await litellm_service.delete_model(model.model_id)
            
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
                    assoc.sync_error = _scrub_secrets(str(e), model.litellm_params if model else None)
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

