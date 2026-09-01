"""Read-only admin surface for the model catalog.

The catalog is authored in the amazeeai-model-catalog repo and applied via
POST /admin/models/apply (app/api/admin_model_apply.py) — the only write
path. These endpoints back the admin dashboard: inventory, per-region sync
status and drift. The credential-redaction helpers here are shared with the
apply endpoint (credentials are write-only: literal keys never leave the
API, os.environ/ references are readable config, and the "********" sentinel
in incoming params means "keep the stored value").
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from sqlalchemy import func

from app.db.database import get_db
from app.core.security import get_role_min_system_admin
from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBRegion,
    DBUser,
)
from app.schemas.models import (
    AdminModelAliasTarget,
    AdminModelRegionResponse,
    AdminModelResponse,
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models", tags=["admin_models"])


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
        upstream_eol=db_model.upstream_eol,
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
