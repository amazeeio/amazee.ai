from fastapi import APIRouter, Depends, Query, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, UTC
from sqlalchemy import func

from app.db.database import get_db
from app.core.security import get_role_min_system_admin
from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAliasTarget,
    DBModelRegion,
    DBRegion,
    DBUser,
)
from app.schemas.models import (
    AdminModelAliasTarget,
    AdminModelCreate,
    AdminModelImport,
    AdminModelImportAllRequest,
    AdminModelImportAllResponse,
    AdminModelRegionResponse,
    AdminModelRegionToggleRequest,
    AdminModelResponse,
    AdminModelUpdate,
    BedrockCandidate,
    BedrockCandidatesResponse,
    ImportableModelResponse,
)
from app.services.model_sync import sync_model_to_region_task
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models", tags=["admin_models"])


def _model_access_groups_map(
    db: Session, model_pks: Optional[List[int]] = None
) -> dict[int, List[tuple[int, str]]]:
    """model pk -> [(group_id, slug), ...] in one query."""
    query = (
        db.query(DBModelAccessGroupModel.model_id, DBModelAccessGroup.id, DBModelAccessGroup.slug)
        .join(DBModelAccessGroup, DBModelAccessGroup.id == DBModelAccessGroupModel.group_id)
        .order_by(DBModelAccessGroup.slug)
    )
    if model_pks is not None:
        query = query.filter(DBModelAccessGroupModel.model_id.in_(model_pks))
    result: dict[int, List[tuple[int, str]]] = {}
    for model_pk, group_id, slug in query.all():
        result.setdefault(model_pk, []).append((group_id, slug))
    return result


def _build_model_response(
    db: Session,
    db_model: DBModel,
    all_regions: Optional[List[DBRegion]] = None,
    mask_litellm_params: bool = True,
    groups_map: Optional[dict] = None,
) -> AdminModelResponse:
    """Helper to assemble a complete AdminModelResponse with all active regions."""
    # Fetch all regions in the database if not pre-cached
    if all_regions is None:
        all_regions = db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()
    if groups_map is None:
        groups_map = _model_access_groups_map(db, [db_model.id])
    model_groups = groups_map.get(db_model.id, [])
    
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
                    litellm_params_override=(
                        None
                        if mask_litellm_params
                        else _redact_litellm_params(mr_record.litellm_params_override)
                    ),
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
        litellm_params=None if mask_litellm_params else _redact_litellm_params(db_model.litellm_params),
        created_at=db_model.created_at,
        updated_at=db_model.updated_at,
        deleted_at=db_model.deleted_at,
        regions=regions_list,
        access_group_ids=[gid for gid, _ in model_groups],
        access_group_slugs=[slug for _, slug in model_groups],
        is_alias=db_model.is_alias,
        alias_targets=[
            AdminModelAliasTarget(region_id=t.region_id, target_model_id=t.target_model_id)
            for t in db_model.alias_targets
        ],
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
    groups_map = _model_access_groups_map(db)
    return [
        _build_model_response(
            db, m, all_regions=all_regions, mask_litellm_params=True, groups_map=groups_map
        )
        for m in db_models
    ]


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

    # A literal sentinel here is pasted-from-a-redacted-response, not a credential —
    # storing it would push "********" to LiteLLM as the real key.
    if _contains_sentinel(model_in.litellm_params):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"litellm_params contains redacted placeholder values ('{CREDENTIAL_SENTINEL}'); provide real credentials."
        )

    if model_in.access_group_ids:
        _validate_access_group_ids(db, model_in.access_group_ids)

    if model_in.is_alias and model_in.litellm_params:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Alias models take their params from the per-region target; litellm_params must be empty.",
        )
    if model_in.alias_targets and not model_in.is_alias:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="alias_targets is only valid for alias models (is_alias=true).",
        )
    if model_in.alias_targets:
        _validate_alias_targets(db, model_in.alias_targets)

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
        litellm_params=None if model_in.is_alias else model_in.litellm_params,
        is_alias=model_in.is_alias,
    )

    db.add(db_model)
    db.flush()
    # No region sync needed here: the model has no region associations yet, so
    # tags get pushed by the first region-toggle sync.
    for group_id in set(model_in.access_group_ids or []):
        db.add(DBModelAccessGroupModel(group_id=group_id, model_id=db_model.id))
    for target in model_in.alias_targets or []:
        db.add(
            DBModelAliasTarget(
                alias_model_id=db_model.id,
                region_id=target.region_id,
                target_model_id=target.target_model_id,
            )
        )
    if model_in.region_overrides:
        # Stored on (inactive) associations now; the first region-toggle sync
        # pushes them.
        _apply_region_overrides(db, db_model, model_in.region_overrides)
    db.commit()
    db.refresh(db_model)

    return _build_model_response(db, db_model)


def _validate_alias_targets(db: Session, targets: List[AdminModelAliasTarget]) -> None:
    """Alias targets must be existing, non-deleted, non-alias models in known regions."""
    region_ids = [t.region_id for t in targets]
    if len(set(region_ids)) != len(region_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Duplicate region in alias_targets: one target per region.",
        )
    found_regions = {
        row[0] for row in db.query(DBRegion.id).filter(DBRegion.id.in_(region_ids)).all()
    }
    missing_regions = sorted(set(region_ids) - found_regions)
    if missing_regions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region ids in alias_targets: {missing_regions}",
        )
    target_ids = {t.target_model_id for t in targets}
    targets_found = (
        db.query(DBModel.id, DBModel.is_alias)
        .filter(DBModel.id.in_(target_ids), DBModel.deleted_at.is_(None))
        .all()
    )
    found_map = {row[0]: row[1] for row in targets_found}
    missing = sorted(target_ids - set(found_map))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown or deleted target model ids in alias_targets: {missing}",
        )
    alias_targets = sorted(mid for mid, is_alias in found_map.items() if is_alias)
    if alias_targets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Alias chains are not supported; target model ids {alias_targets} are aliases.",
        )


def _replace_alias_targets(
    db: Session, alias_model: DBModel, targets: List[AdminModelAliasTarget]
) -> bool:
    """Replace an alias's per-region targets. Returns True if they changed."""
    _validate_alias_targets(db, targets)
    existing = {
        (t.region_id, t.target_model_id)
        for t in db.query(DBModelAliasTarget).filter_by(alias_model_id=alias_model.id).all()
    }
    new = {(t.region_id, t.target_model_id) for t in targets}
    if existing == new:
        return False
    db.query(DBModelAliasTarget).filter_by(alias_model_id=alias_model.id).delete()
    for target in targets:
        db.add(
            DBModelAliasTarget(
                alias_model_id=alias_model.id,
                region_id=target.region_id,
                target_model_id=target.target_model_id,
            )
        )
    return True


def _apply_region_overrides(
    db: Session, db_model: DBModel, overrides: dict[int, dict]
) -> set[int]:
    """Upsert per-region litellm_params overrides. Returns region ids whose
    stored override actually changed. Sentinels resolve against the stored
    override (credentials are write-only, same as litellm_params)."""
    changed: set[int] = set()
    for region_id, override in overrides.items():
        region = db.query(DBRegion).filter(DBRegion.id == region_id).first()
        if not region:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown region id in region_overrides: {region_id}",
            )
        assoc = (
            db.query(DBModelRegion)
            .filter_by(model_id=db_model.id, region_id=region_id)
            .first()
        )
        stored = assoc.litellm_params_override if assoc else None
        resolved = _merge_credential_sentinels(override or {}, stored) or None
        if assoc:
            if assoc.litellm_params_override != resolved:
                assoc.litellm_params_override = resolved
                changed.add(region_id)
        elif resolved:
            # Override supplied before the region was ever toggled: store it on
            # an inactive association so the first toggle picks it up.
            db.add(
                DBModelRegion(
                    model_id=db_model.id,
                    region_id=region_id,
                    is_active=False,
                    sync_status="not_configured",
                    litellm_params_override=resolved,
                )
            )
    return changed


def _validate_access_group_ids(db: Session, group_ids: List[int]) -> None:
    found = {
        row[0]
        for row in db.query(DBModelAccessGroup.id)
        .filter(DBModelAccessGroup.id.in_(group_ids))
        .all()
    }
    missing = sorted(set(group_ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown access group ids: {missing}",
        )


def _replace_model_access_groups(db: Session, model_pk: int, group_ids: List[int]) -> bool:
    """Replace a model's group memberships. Returns True if they changed."""
    _validate_access_group_ids(db, group_ids)
    existing = {
        row[0]
        for row in db.query(DBModelAccessGroupModel.group_id)
        .filter(DBModelAccessGroupModel.model_id == model_pk)
        .all()
    }
    new_ids = set(group_ids)
    if existing == new_ids:
        return False
    db.query(DBModelAccessGroupModel).filter_by(model_id=model_pk).delete()
    for group_id in new_ids:
        db.add(DBModelAccessGroupModel(group_id=group_id, model_id=model_pk))
    return True


def _extract_credential_keys(litellm_params: dict) -> List[str]:
    if not litellm_params:
        return []
    cred_keys = []
    for k, v in litellm_params.items():
        if _is_credential_key(k):
            cred_keys.append(k)
        elif isinstance(v, str) and v.startswith("os.environ/"):
            cred_keys.append(k)
    return sorted(list(set(cred_keys)))


CREDENTIAL_SENTINEL = "********"

_CRED_KEYWORDS = {"key", "secret", "token", "password", "credential"}


def _is_credential_key(key: str) -> bool:
    return any(kw in key.lower() for kw in _CRED_KEYWORDS)


def _redact_litellm_params(litellm_params: Optional[dict]) -> Optional[dict]:
    """Replace credential values with a sentinel so secrets never reach the browser.
    Walks nested dicts (e.g. extra_headers, vertex_credentials wrappers).
    os.environ/ values are references to proxy-side env vars, not secrets — keep them readable.
    Only string values are redacted: keyword matching is broad (e.g. 'token' also
    hits 'max_tokens'), so numeric config values must pass through.
    """
    if not litellm_params:
        return litellm_params

    def walk(d: dict) -> dict:
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out[k] = walk(v)
            elif isinstance(v, str) and v.startswith("os.environ/"):
                out[k] = v
            elif isinstance(v, str) and _is_credential_key(k):
                out[k] = CREDENTIAL_SENTINEL
            else:
                out[k] = v
        return out

    return walk(litellm_params)


def _merge_credential_sentinels(new_params: dict, stored_params: Optional[dict]) -> dict:
    """Resolve sentinel values in a client-submitted params dict against the stored
    params (credentials are write-only: a sentinel means 'keep the stored value').
    A sentinel with no stored counterpart is dropped rather than persisted as None.
    """
    stored = stored_params if isinstance(stored_params, dict) else {}
    out = {}
    for k, v in new_params.items():
        if v == CREDENTIAL_SENTINEL:
            if k in stored:
                out[k] = stored[k]
        elif isinstance(v, dict):
            out[k] = _merge_credential_sentinels(v, stored.get(k))
        else:
            out[k] = v
    return out


def _contains_sentinel(params: Optional[dict]) -> bool:
    for v in (params or {}).values():
        if v == CREDENTIAL_SENTINEL:
            return True
        if isinstance(v, dict) and _contains_sentinel(v):
            return True
    return False


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
        parsed = _parse_importable_entry(entry)
        if not parsed:
            continue

        # Exclude models that already exist in active state in database
        if parsed["model_id"] in active_model_ids:
            continue

        litellm_params = parsed["litellm_params"]
        cred_keys = _extract_credential_keys(litellm_params)
        model_name = parsed["model_id"]
        display_name = parsed["display_name"]
        provider = parsed["provider"]
        model_type = parsed["type"]
        context_length = parsed["context_length"]
        max_output_tokens = parsed["max_output_tokens"]
        description = parsed["description"]

        importable_list.append(
            ImportableModelResponse(
                model_id=model_name,
                display_name=display_name,
                provider=provider,
                type=model_type,
                context_length=context_length,
                max_output_tokens=max_output_tokens,
                description=description,
                litellm_params=_redact_litellm_params(litellm_params),
                credential_keys=cred_keys,
            )
        )

    return importable_list


def _parse_importable_entry(entry) -> Optional[dict]:
    """Normalize one LiteLLM /model/info entry into importable-model fields.
    Returns raw (unredacted) litellm_params — callers redact for responses."""
    if not isinstance(entry, dict):
        return None
    model_name = entry.get("model_name")
    if not model_name:
        return None

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

    # model_info.metadata is arbitrary JSON — commonly a dict in production.
    # Only use it as the description when it is actually a string.
    raw_metadata = model_info.get("metadata")
    description = (
        raw_metadata
        if isinstance(raw_metadata, str) and raw_metadata
        else f"Imported model {model_name} from LiteLLM proxy."
    )

    return {
        "model_id": model_name,
        "display_name": display_name,
        "provider": provider,
        "type": model_type,
        "context_length": model_info.get("max_input_tokens") or model_info.get("max_tokens"),
        "max_output_tokens": model_info.get("max_output_tokens"),
        "description": description,
        "litellm_params": litellm_params,
    }


@router.post(
    "/import-all",
    response_model=AdminModelImportAllResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_all_models(
    import_in: AdminModelImportAllRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Bulk-import every model configured on a region's LiteLLM proxy that is
    not yet in the DB inventory. Each import marks the source region synced
    (params come from the proxy itself).

    Temporary migration helper for porting YAML-configured prod models to
    DB-managed models — remove once that migration is done.
    """
    region = db.query(DBRegion).filter(
        DBRegion.id == import_in.region_id, DBRegion.is_active.is_(True)
    ).first()
    if not region:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active Region with ID {import_in.region_id} not found.",
        )
    if import_in.access_group_ids:
        _validate_access_group_ids(db, import_in.access_group_ids)

    from app.services.litellm import LiteLLMService
    litellm_service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    try:
        litellm_data = await litellm_service.get_model_info()
    except Exception as e:
        logger.error(f"Failed to fetch model info from LiteLLM in region {region.name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with LiteLLM proxy in region '{region.name}': {str(e)}",
        )

    model_entries = litellm_data.get("data", [])
    if not isinstance(model_entries, list):
        model_entries = []

    active_model_ids = {
        row[0]
        for row in db.query(DBModel.model_id).filter(DBModel.deleted_at.is_(None)).all()
    }

    imported: list[str] = []
    skipped: list[str] = []
    errors: dict[str, str] = {}
    seen: set[str] = set()
    for entry in model_entries:
        parsed = _parse_importable_entry(entry)
        if not parsed:
            continue
        name = parsed["model_id"]
        if name in seen:
            continue  # multiple deployments of the same model_name
        seen.add(name)
        if name in active_model_ids:
            skipped.append(name)
            continue
        try:
            db_model = DBModel(
                model_id=name,
                display_name=parsed["display_name"],
                provider=parsed["provider"],
                type=parsed["type"],
                context_length=parsed["context_length"],
                max_output_tokens=parsed["max_output_tokens"],
                description=parsed["description"],
                is_active_globally=True,
                litellm_params=parsed["litellm_params"],
            )
            db.add(db_model)
            db.flush()
            for group_id in set(import_in.access_group_ids or []):
                db.add(DBModelAccessGroupModel(group_id=group_id, model_id=db_model.id))
            assoc = DBModelRegion(
                model_id=db_model.id,
                region_id=region.id,
                is_active=True,
                sync_status="synced",
                synced_at=datetime.now(UTC),
            )
            db.add(assoc)
            # Freshly assigned group tags only exist in our DB — push them.
            if import_in.access_group_ids:
                assoc.sync_status = "pending"
                assoc.synced_at = None
                background_tasks.add_task(sync_model_to_region_task, db_model.id, region.id)
            db.commit()
            imported.append(name)
        except Exception as e:
            db.rollback()
            errors[name] = str(e)
            logger.error(f"Bulk import of '{name}' from region {region.name} failed: {e}")

    logger.info(
        f"Bulk import from region {region.name}: {len(imported)} imported, "
        f"{len(skipped)} skipped, {len(errors)} failed"
    )
    return AdminModelImportAllResponse(imported=imported, skipped=skipped, errors=errors)


# Which AWS regions count as "in area" for each of our regional areas — used
# by the UI to highlight bedrock candidates that fit a LiteLLM region.
_AREA_AWS_REGIONS: dict[str, List[str]] = {
    "US": ["us-east-1", "us-east-2", "us-west-2"],
    "US+CA": ["us-east-1", "us-east-2", "us-west-2", "ca-central-1"],
    "EU": ["eu-central-1", "eu-central-2", "eu-west-1", "eu-west-2", "eu-west-3", "eu-north-1", "eu-south-1", "eu-south-2"],
    "DE": ["eu-central-1"],
    "CH": ["eu-central-2"],
    "UK": ["eu-west-2"],
    "AU": ["ap-southeast-2"],
    "APAC": ["ap-northeast-1", "ap-northeast-2", "ap-northeast-3", "ap-south-1", "ap-south-2", "ap-southeast-1", "ap-southeast-2"],
    "GLOBAL": [],
}


@router.get("/bedrock-candidates", response_model=BedrockCandidatesResponse)
async def bedrock_candidates(
    q: str = Query(..., min_length=2, description="Search upstream Bedrock model ids/names"),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Search the upstream Bedrock catalog for candidate backend model ids.

    Returns every ACTIVE catalog entry matching the query with the AWS regions
    it runs in, so an admin can pick the exact id per LiteLLM region instead of
    relying on prefix conventions (us./eu./...) that AWS does not keep stable.
    """
    from app.api.public import _fetch_bedrock_catalog
    from app.core.config import settings

    catalog = await _fetch_bedrock_catalog(settings.BEDROCK_MODELS_URL)
    ql = q.lower()
    candidates: list[BedrockCandidate] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        model_id = item.get("modelId")
        if not isinstance(model_id, str) or not model_id:
            continue
        model_name = str(item.get("modelName") or model_id)
        if ql not in model_id.lower() and ql not in model_name.lower():
            continue
        lifecycle = item.get("modelLifecycle") or {}
        lifecycle_status = lifecycle.get("status") if isinstance(lifecycle, dict) else None
        if lifecycle_status and lifecycle_status != "ACTIVE":
            continue
        regions_raw = item.get("regions")
        regions = (
            sorted(r for r in regions_raw if isinstance(r, str))
            if isinstance(regions_raw, (list, tuple))
            else []
        )
        candidates.append(
            BedrockCandidate(
                model_id=model_id,
                model_name=model_name,
                provider_name=str(item.get("providerName") or "Unknown"),
                regions=regions,
            )
        )
        if len(candidates) >= 100:
            break

    return BedrockCandidatesResponse(
        query=q, candidates=candidates, area_aws_regions=_AREA_AWS_REGIONS
    )


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

    # Group memberships live in their own table, not on DBModel — pop before
    # the setattr loop. Explicit [] means "remove from all groups".
    groups_changed = False
    group_ids = update_data.pop("access_group_ids", None)
    if group_ids is not None:
        groups_changed = _replace_model_access_groups(db, db_model.id, group_ids)

    # Alias targets and per-region overrides also live in their own tables.
    aliases_changed = False
    alias_targets = update_data.pop("alias_targets", None)
    if alias_targets is not None:
        if not db_model.is_alias:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="alias_targets is only valid for alias models.",
            )
        aliases_changed = _replace_alias_targets(
            db, db_model, [AdminModelAliasTarget(**t) for t in alias_targets]
        )

    overrides_changed: set[int] = set()
    region_overrides = update_data.pop("region_overrides", None)
    if region_overrides is not None:
        overrides_changed = _apply_region_overrides(db, db_model, region_overrides)

    # Credentials are write-only: reads return CREDENTIAL_SENTINEL, so a sentinel
    # coming back on update means "keep the stored value".
    if update_data.get("litellm_params"):
        if db_model.is_alias:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Alias models take their params from the per-region target; litellm_params must be empty.",
            )
        update_data["litellm_params"] = _merge_credential_sentinels(
            update_data["litellm_params"], db_model.litellm_params
        )

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
    if is_active_changed or (
        db_model.is_active_globally
        and (litellm_params_changed or groups_changed or aliases_changed)
    ):
        for assoc in db_model.regions:
            if assoc.is_active:
                assoc.sync_status = "pending"
                assoc.sync_error = None
                background_tasks.add_task(sync_model_to_region_task, db_model.id, assoc.region_id)
        db.commit()
    elif overrides_changed and db_model.is_active_globally:
        # Override-only change: re-sync just the affected regions.
        for assoc in db_model.regions:
            if assoc.is_active and assoc.region_id in overrides_changed:
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

    # An alias pointing at this model would silently break on its next sync —
    # retarget or delete those aliases first.
    referencing = (
        db.query(DBModel.model_id)
        .join(DBModelAliasTarget, DBModelAliasTarget.alias_model_id == DBModel.id)
        .filter(
            DBModelAliasTarget.target_model_id == db_model.id,
            DBModel.deleted_at.is_(None),
        )
        .distinct()
        .all()
    )
    if referencing:
        names = sorted(row[0] for row in referencing)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete: alias model(s) {names} point at this model. Retarget them first.",
        )

    db_model.deleted_at = datetime.now(UTC)

    # Drop group memberships so the deleted model no longer counts toward any
    # group; a later import-restore starts ungrouped (reachable by no team).
    db.query(DBModelAccessGroupModel).filter_by(model_id=db_model.id).delete()
    # An alias's own targets are meaningless once it is deleted.
    db.query(DBModelAliasTarget).filter_by(alias_model_id=db_model.id).delete()

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

    # An alias can only be enabled in regions where it has a target.
    if toggle_in.is_active and db_model.is_alias:
        has_target = (
            db.query(DBModelAliasTarget)
            .filter_by(alias_model_id=db_model.id, region_id=toggle_in.region_id)
            .first()
        )
        if not has_target:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Alias '{db_model.model_id}' has no target model for region '{db_region.name}'.",
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

    # Optional per-region params override rides along with the toggle
    # (credentials are write-only: sentinels resolve against the stored value).
    if toggle_in.litellm_params_override is not None:
        association.litellm_params_override = (
            _merge_credential_sentinels(
                toggle_in.litellm_params_override, association.litellm_params_override
            )
            or None
        )
        
    db.commit()
    
    # Trigger async sync background task
    background_tasks.add_task(sync_model_to_region_task, toggle_in.model_id, toggle_in.region_id)
    
    return {"status": "success", "message": "Regional active state updated. Synchronization scheduled."}

@router.post("/import", response_model=AdminModelResponse, status_code=status.HTTP_201_CREATED)
async def import_model(
    import_in: AdminModelImport,
    background_tasks: BackgroundTasks,
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

    # Fetch litellm_params server-side from the region's proxy. This both verifies
    # the model actually exists there (so "synced" is honest) and keeps credentials
    # out of the browser round-trip — client-supplied litellm_params are ignored.
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
    proxy_entry = None
    if isinstance(model_entries, list):
        proxy_entry = next(
            (entry for entry in model_entries
             if isinstance(entry, dict) and entry.get("model_name") == import_in.model_id),
            None,
        )
    if not proxy_entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{import_in.model_id}' not found in region '{region.name}'."
        )
    litellm_params = proxy_entry.get("litellm_params", {}) or {}

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
        db_model.litellm_params = litellm_params
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
            litellm_params=litellm_params,
        )
        db.add(db_model)

    # Flush (not commit) so db_model.id is assigned and the model + association
    # commit atomically below.
    db.flush()

    groups_assigned = False
    if import_in.access_group_ids is not None:
        groups_assigned = _replace_model_access_groups(
            db, db_model.id, import_in.access_group_ids
        )

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

    # Import copies the proxy's state, but freshly assigned access-group tags
    # only exist in our DB — push them so "synced" stays honest.
    if groups_assigned:
        association.sync_status = "pending"
        association.synced_at = None
        background_tasks.add_task(
            sync_model_to_region_task, db_model.id, import_in.region_id
        )

    db.commit()
    db.refresh(db_model)

    return _build_model_response(db, db_model)
