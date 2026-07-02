from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, UTC
from sqlalchemy import func

from app.db.database import get_db
from app.core.security import get_role_min_system_admin
from app.db.models import DBModel, DBModelRegion, DBRegion, DBUser
from app.schemas.models import (
    AdminModelCreate,
    AdminModelUpdate,
    AdminModelResponse,
    AdminModelRegionResponse,
    AdminModelRegionToggleRequest,
    ImportableModelResponse,
    AdminModelImport,
)
from app.services.model_sync import sync_model_to_region_task
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models", tags=["admin_models"])


def _build_model_response(
    db: Session,
    db_model: DBModel,
    all_regions: Optional[List[DBRegion]] = None,
    mask_litellm_params: bool = True,
) -> AdminModelResponse:
    """Helper to assemble a complete AdminModelResponse with all active regions."""
    # Fetch all regions in the database if not pre-cached
    if all_regions is None:
        all_regions = db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()
    
    # Map of region_id -> DBModelRegion record for this model
    model_regions_map = {mr.region_id: mr for mr in db_model.regions}
    
    regions_list = []
    for reg in all_regions:
        mr_record = model_regions_map.get(reg.id)
        if mr_record:
            regions_list.append(
                AdminModelRegionResponse(
                    region_id=reg.id,
                    region_name=reg.name,
                    is_active=mr_record.is_active,
                    sync_status=mr_record.sync_status,
                    sync_error=mr_record.sync_error,
                    synced_at=mr_record.synced_at,
                )
            )
        else:
            # Region not associated yet, return defaults
            regions_list.append(
                AdminModelRegionResponse(
                    region_id=reg.id,
                    region_name=reg.name,
                    is_active=False,
                    sync_status="not_configured",
                    sync_error=None,
                    synced_at=None,
                )
            )
            
    return AdminModelResponse(
        id=db_model.id,
        model_id=db_model.model_id,
        display_name=db_model.display_name,
        provider=db_model.provider,
        type=db_model.type,
        context_length=db_model.context_length,
        max_output_tokens=db_model.max_output_tokens,
        description=db_model.description,
        real_eol=db_model.real_eol,
        override_eol=db_model.override_eol,
        is_active_globally=db_model.is_active_globally,
        litellm_params=None if mask_litellm_params else db_model.litellm_params,
        created_at=db_model.created_at,
        updated_at=db_model.updated_at,
        deleted_at=db_model.deleted_at,
        regions=regions_list,
    )


@router.get("", response_model=List[AdminModelResponse])
async def list_models(
    search: Optional[str] = Query(None, description="Search models by model_id or display_name"),
    provider: Optional[str] = Query(None, description="Filter models by provider"),
    include_deleted: bool = Query(False, description="Include soft-deleted models"),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    List all models in the global inventory.
    Only accessible by system administrators.
    """
    query = db.query(DBModel)
    
    if not include_deleted:
        query = query.filter(DBModel.deleted_at.is_(None))
        
    if search:
        search_escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_pattern = f"%{search_escaped}%"
        query = query.filter(
            (DBModel.model_id.ilike(search_pattern, escape="\\")) | 
            (DBModel.display_name.ilike(search_pattern, escape="\\"))
        )
        
    if provider:
        query = query.filter(func.lower(DBModel.provider) == provider.lower())
        
    db_models = query.order_by(DBModel.created_at.desc()).all()
    all_regions = db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()
    return [_build_model_response(db, m, all_regions=all_regions, mask_litellm_params=True) for m in db_models]


@router.post("", response_model=AdminModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    model_in: AdminModelCreate,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Create a new model in the global inventory.
    Only accessible by system administrators.
    """
    # Check if model_id already exists
    existing_model = db.query(DBModel).filter(
        DBModel.model_id == model_in.model_id,
        DBModel.deleted_at.is_(None)
    ).first()
    if existing_model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model with ID '{model_in.model_id}' already exists."
        )

    # Validate EOL logic
    if model_in.override_eol and model_in.real_eol:
        if model_in.override_eol > model_in.real_eol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Override EOL cannot be set after Real EOL date."
            )

    db_model = DBModel(
        model_id=model_in.model_id,
        display_name=model_in.display_name,
        provider=model_in.provider,
        type=model_in.type,
        context_length=model_in.context_length,
        max_output_tokens=model_in.max_output_tokens,
        description=model_in.description,
        real_eol=model_in.real_eol,
        override_eol=model_in.override_eol,
        is_active_globally=model_in.is_active_globally,
        litellm_params=model_in.litellm_params,
    )
    
    db.add(db_model)
    db.commit()
    db.refresh(db_model)
    
    return _build_model_response(db, db_model)


def _extract_credential_keys(litellm_params: dict) -> List[str]:
    if not litellm_params:
        return []
    cred_keywords = {"key", "secret", "token", "password", "aws_access_key_id", "aws_secret_access_key", "api_key"}
    cred_keys = []
    for k, v in litellm_params.items():
        k_lower = k.lower()
        if any(kw in k_lower for kw in cred_keywords):
            cred_keys.append(k)
        elif isinstance(v, str) and v.startswith("os.environ/"):
            cred_keys.append(k)
    return sorted(list(set(cred_keys)))


@router.get("/importable", response_model=List[ImportableModelResponse])
async def list_importable_models(
    region_id: int = Query(..., description="The region ID to inspect for existing LiteLLM models"),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    List models currently configured in the regional LiteLLM proxy but not yet in the DB inventory.
    Only accessible by system administrators.
    """
    region = db.query(DBRegion).filter(DBRegion.id == region_id, DBRegion.is_active.is_(True)).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {region_id} not found."
        )

    from app.services.litellm import LiteLLMService
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url,
        api_key=region.litellm_api_key
    )

    try:
        litellm_data = await litellm_service.get_model_info()
    except Exception as e:
        logger.error(f"Failed to fetch model info from LiteLLM in region {region.name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with LiteLLM proxy in region '{region.name}': {str(e)}"
        )

    model_entries = litellm_data.get("data", [])
    if not isinstance(model_entries, list):
        return []

    # Get active model_ids in database
    active_models = db.query(DBModel).filter(DBModel.deleted_at.is_(None)).all()
    active_model_ids = {m.model_id for m in active_models}

    importable_list = []
    for entry in model_entries:
        model_name = entry.get("model_name")
        if not model_name:
            continue

        # Exclude models that already exist in active state in database
        if model_name in active_model_ids:
            continue

        litellm_params = entry.get("litellm_params", {}) or {}
        model_info = entry.get("model_info", {}) or {}

        # Infer provider
        provider = "unknown"
        if "litellm_provider" in model_info:
            provider = model_info["litellm_provider"]
        elif "custom_llm_provider" in litellm_params:
            provider = litellm_params["custom_llm_provider"]
        else:
            underlying_model = litellm_params.get("model", "")
            if underlying_model and "/" in underlying_model:
                provider = underlying_model.split("/", 1)[0]

        # Normalize provider name
        provider_lower = provider.lower()
        if "bedrock" in provider_lower:
            provider = "aws"
        elif "vertex" in provider_lower or "gemini" in provider_lower:
            provider = "google"
        elif "azure" in provider_lower:
            provider = "azure"
        elif "openai" in provider_lower:
            provider = "openai"
        elif "anthropic" in provider_lower:
            provider = "anthropic"

        # Infer type/mode
        mode = model_info.get("mode", "chat")
        if mode == "embedding" or "embed" in model_name.lower():
            model_type = "embedding"
        else:
            model_type = "chat"

        # Prettify display name
        base_model = model_info.get("base_model")
        if base_model:
            display_name = base_model.replace("-", " ").replace("_", " ").title()
        else:
            display_name = model_name.replace("-", " ").replace("_", " ").title()

        context_length = model_info.get("max_input_tokens") or model_info.get("max_tokens")
        max_output_tokens = model_info.get("max_output_tokens")
        description = model_info.get("metadata") or f"Imported model {model_name} from LiteLLM proxy."

        cred_keys = _extract_credential_keys(litellm_params)

        importable_list.append(
            ImportableModelResponse(
                model_id=model_name,
                display_name=display_name,
                provider=provider,
                type=model_type,
                context_length=context_length,
                max_output_tokens=max_output_tokens,
                description=description,
                litellm_params=litellm_params,
                credential_keys=cred_keys,
            )
        )

    return importable_list


@router.get("/{model_id_or_id:path}", response_model=AdminModelResponse)
async def get_model(
    model_id_or_id: str,
    include_deleted: bool = Query(False, description="Include soft-deleted models"),
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Retrieve details of a single model by primary ID or model_id string.
    Only accessible by system administrators.
    """
    query = db.query(DBModel)
    
    if not include_deleted:
        query = query.filter(DBModel.deleted_at.is_(None))
    
    # Try looking up as integer primary key first if digits
    if model_id_or_id.isdigit():
        db_model = query.filter(DBModel.id == int(model_id_or_id)).first()
    else:
        db_model = query.filter(DBModel.model_id == model_id_or_id).first()
        
    if not db_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model '{model_id_or_id}' not found."
        )
        
    return _build_model_response(db, db_model, mask_litellm_params=False)


@router.put("/{id}", response_model=AdminModelResponse)
async def update_model(
    id: int,
    model_in: AdminModelUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Update global model metadata, config, or EOL dates.
    Only accessible by system administrators.
    """
    db_model = db.query(DBModel).filter(
        DBModel.id == id,
        DBModel.deleted_at.is_(None)
    ).first()
    
    if not db_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model with ID {id} not found."
        )

    update_data = model_in.model_dump(exclude_unset=True)
    
    # Validate date constraints if dates are being modified
    target_real_eol = update_data.get("real_eol", db_model.real_eol)
    target_override_eol = update_data.get("override_eol", db_model.override_eol)
    
    if target_override_eol and target_real_eol:
        if target_override_eol > target_real_eol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Override EOL cannot be set after Real EOL date."
            )

    old_is_active_globally = db_model.is_active_globally

    for field, value in update_data.items():
        setattr(db_model, field, value)
        
    db_model.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(db_model)
    
    # Re-sync only when LiteLLM-relevant fields changed or global active state toggled
    is_active_changed = (old_is_active_globally != db_model.is_active_globally)
    litellm_params_changed = "litellm_params" in update_data
    if is_active_changed or (db_model.is_active_globally and litellm_params_changed):
        for assoc in db_model.regions:
            if assoc.is_active:
                assoc.sync_status = "pending"
                assoc.sync_error = None
                background_tasks.add_task(sync_model_to_region_task, db_model.id, assoc.region_id)
        db.commit()
    
    return _build_model_response(db, db_model)


@router.delete("/{id}", response_model=AdminModelResponse)
async def delete_model(
    id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Soft-delete a model from the global inventory to maintain audit integrity.
    Only accessible by system administrators.
    """
    db_model = db.query(DBModel).filter(
        DBModel.id == id,
        DBModel.deleted_at.is_(None)
    ).first()
    
    if not db_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model with ID {id} not found."
        )

    db_model.deleted_at = datetime.now(UTC)
    
    # Soft delete regional associations and trigger sync task to de-register (delete) from LiteLLM
    for assoc in db_model.regions:
        if assoc.is_active or (not assoc.is_active and assoc.sync_status == "failed"):
            assoc.is_active = False
            assoc.sync_status = "pending"
            assoc.sync_error = None
            background_tasks.add_task(sync_model_to_region_task, db_model.id, assoc.region_id)
            
    db.commit()
    db.refresh(db_model)
    
    return _build_model_response(db, db_model)


@router.post("/region-toggle", status_code=status.HTTP_200_OK)
async def toggle_model_region(
    toggle_in: AdminModelRegionToggleRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Toggle a model active status in a specific region.
    Creates or updates the association record in the DB and marks sync as pending.
    Only accessible by system administrators.
    """
    # Verify model exists
    db_model = db.query(DBModel).filter(
        DBModel.id == toggle_in.model_id,
        DBModel.deleted_at.is_(None)
    ).first()
    if not db_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Model with ID {toggle_in.model_id} not found."
        )
        
    # Verify region exists and is active
    db_region = db.query(DBRegion).filter(
        DBRegion.id == toggle_in.region_id,
        DBRegion.is_active.is_(True)
    ).first()
    if not db_region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {toggle_in.region_id} not found."
        )
        
    # Reject enabling a region for a globally-inactive model — the sync task would
    # immediately deregister it, leaving is_active=True but model absent from LiteLLM.
    if toggle_in.is_active and not db_model.is_active_globally:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot enable a region for a model that is globally inactive."
        )
        
    # Create or update junction record
    association = db.query(DBModelRegion).filter(
        DBModelRegion.model_id == toggle_in.model_id,
        DBModelRegion.region_id == toggle_in.region_id
    ).first()
    
    if association:
        association.is_active = toggle_in.is_active
        association.sync_status = "pending"
        association.sync_error = None
        association.updated_at = datetime.now(UTC)
    else:
        association = DBModelRegion(
            model_id=toggle_in.model_id,
            region_id=toggle_in.region_id,
            is_active=toggle_in.is_active,
            sync_status="pending",
        )
        db.add(association)
        
    db.commit()
    
    # Trigger async sync background task
    background_tasks.add_task(sync_model_to_region_task, toggle_in.model_id, toggle_in.region_id)
    
    return {"status": "success", "message": "Regional active state updated. Synchronization scheduled."}

@router.post("/import", response_model=AdminModelResponse, status_code=status.HTTP_201_CREATED)
async def import_model(
    import_in: AdminModelImport,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """
    Import a model from a regional LiteLLM instance into the global model inventory,
    marking the region association as immediately synchronized.
    Only accessible by system administrators.
    """
    # Verify region exists and is active
    region = db.query(DBRegion).filter(DBRegion.id == import_in.region_id, DBRegion.is_active.is_(True)).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {import_in.region_id} not found."
        )

    # Check if a non-soft-deleted model with this model_id already exists
    existing_model = db.query(DBModel).filter(
        DBModel.model_id == import_in.model_id,
        DBModel.deleted_at.is_(None)
    ).first()
    if existing_model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model with ID '{import_in.model_id}' already exists."
        )

    # Check if a soft-deleted model with this model_id exists, and restore it if so
    soft_deleted_model = db.query(DBModel).filter(
        DBModel.model_id == import_in.model_id,
        DBModel.deleted_at.is_not(None)
    ).first()

    if soft_deleted_model:
        # Restore the model
        db_model = soft_deleted_model
        db_model.deleted_at = None
        db_model.display_name = import_in.display_name
        db_model.provider = import_in.provider
        db_model.type = import_in.type
        db_model.context_length = import_in.context_length
        db_model.max_output_tokens = import_in.max_output_tokens
        db_model.description = import_in.description
        db_model.real_eol = import_in.real_eol
        db_model.override_eol = import_in.override_eol
        db_model.is_active_globally = import_in.is_active_globally
        db_model.litellm_params = import_in.litellm_params
        db_model.updated_at = datetime.now(UTC)
    else:
        # Create a new model
        db_model = DBModel(
            model_id=import_in.model_id,
            display_name=import_in.display_name,
            provider=import_in.provider,
            type=import_in.type,
            context_length=import_in.context_length,
            max_output_tokens=import_in.max_output_tokens,
            description=import_in.description,
            real_eol=import_in.real_eol,
            override_eol=import_in.override_eol,
            is_active_globally=import_in.is_active_globally,
            litellm_params=import_in.litellm_params,
        )
        db.add(db_model)

    db.commit()
    db.refresh(db_model)

    # Upsert the region association and mark as immediately synced,
    # since it was imported directly from this active region proxy.
    association = db.query(DBModelRegion).filter(
        DBModelRegion.model_id == db_model.id,
        DBModelRegion.region_id == import_in.region_id
    ).first()

    if association:
        association.is_active = True
        association.sync_status = "synced"
        association.sync_error = None
        association.synced_at = datetime.now(UTC)
        association.updated_at = datetime.now(UTC)
    else:
        association = DBModelRegion(
            model_id=db_model.id,
            region_id=import_in.region_id,
            is_active=True,
            sync_status="synced",
            sync_error=None,
            synced_at=datetime.now(UTC),
        )
        db.add(association)

    db.commit()
    db.refresh(db_model)

    return _build_model_response(db, db_model)
