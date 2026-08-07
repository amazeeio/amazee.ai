"""Declarative model-config apply: the GitOps entry point.

A config repo (CSV files + GitHub Action) POSTs the full desired state of the
model catalog here. This endpoint diffs it against the DB, upserts access
groups, models, aliases and per-region deployments, and schedules the existing
per-(model, region) sync tasks for anything that changed. With dry_run=true it
reports the diff and rolls back — that powers "what would this PR change"
comments on config-repo pull requests.

Semantics:
- Everything is keyed by stable strings (model_id, group slug, region name).
- For models present in the payload, their deployment list is authoritative:
  a region missing from the file deactivates that deployment.
- Whole models absent from the payload are only deactivated with prune=true,
  and are soft-deactivated (never hard-deleted).
- Access groups are never pruned automatically: deleting a group cascades to
  team attachments, which is runtime state this endpoint does not own.
"""

import logging
from datetime import datetime, UTC
from typing import Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.admin_models import _contains_sentinel, _merge_credential_sentinels
from app.core.config import catalog_manages
from app.core.security import get_role_min_system_admin
from app.db.database import get_db
from app.db.models import (
    DBModel,
    DBModelAccessGroup,
    DBModelAccessGroupModel,
    DBModelAccessGroupRegion,
    DBModelAliasTarget,
    DBModelRegion,
    DBRegion,
    DBUser,
)
from app.schemas.models import (
    ApplyChange,
    ApplyConfigRequest,
    ApplyConfigResponse,
    ApplyModelSpec,
)
from app.services.model_sync import sync_model_to_region_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/models/apply", tags=["admin_models"])


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _validate_specs(req: ApplyConfigRequest) -> None:
    model_ids = [m.model_id for m in req.models]
    dupes = sorted({m for m in model_ids if model_ids.count(m) > 1})
    if dupes:
        raise _bad_request(f"Duplicate model_id in payload: {dupes}")
    slugs = [g.slug for g in req.access_groups]
    dupe_slugs = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupe_slugs:
        raise _bad_request(f"Duplicate access group slug in payload: {dupe_slugs}")
    if req.prune and not req.models:
        raise _bad_request(
            "Refusing to prune with an empty models list — that would deactivate the entire catalog."
        )
    known_slugs = set(slugs)
    for spec in req.models:
        if spec.is_alias:
            if spec.litellm_params:
                raise _bad_request(
                    f"Alias '{spec.model_id}': litellm_params must be empty (params come from the target)."
                )
            if spec.deployments:
                raise _bad_request(
                    f"Alias '{spec.model_id}': deployments are derived from alias_targets; do not list them."
                )
            if not spec.alias_targets:
                raise _bad_request(f"Alias '{spec.model_id}' has no alias_targets.")
            regions = [t.region for t in spec.alias_targets]
            if len(set(regions)) != len(regions):
                raise _bad_request(f"Alias '{spec.model_id}': duplicate region in alias_targets.")
        else:
            if spec.alias_targets:
                raise _bad_request(
                    f"Model '{spec.model_id}': alias_targets is only valid with is_alias=true."
                )
            regions = [d.region for d in spec.deployments]
            if len(set(regions)) != len(regions):
                raise _bad_request(f"Model '{spec.model_id}': duplicate region in deployments.")
        # Groups may only reference slugs defined in the same payload, so the
        # file stays self-contained and reviewable.
        unknown_groups = sorted(set(spec.access_groups) - known_slugs)
        if unknown_groups:
            raise _bad_request(
                f"Model '{spec.model_id}' references access groups not defined in payload: {unknown_groups}"
            )


def _resolve_regions(db: Session, req: ApplyConfigRequest) -> Dict[str, DBRegion]:
    names: Set[str] = set()
    for g in req.access_groups:
        names.update(g.regions)
    for m in req.models:
        names.update(d.region for d in m.deployments)
        names.update(t.region for t in m.alias_targets)
    if not names:
        return {}
    rows = db.query(DBRegion).filter(DBRegion.name.in_(names)).all()
    found = {r.name: r for r in rows}
    missing = sorted(names - set(found))
    if missing:
        raise _bad_request(f"Unknown regions in payload: {missing}")
    inactive = sorted(name for name, r in found.items() if not r.is_active)
    if inactive:
        raise _bad_request(f"Inactive regions in payload: {inactive}")
    # Fail the config-repo PR loudly rather than writing DB rows whose sync the
    # choke point in sync_model_to_region_task would then refuse.
    unmanaged = sorted(name for name in found if not catalog_manages(name))
    if unmanaged:
        raise _bad_request(
            f"Regions not managed by the model catalog: {unmanaged}. "
            "Add them to CATALOG_MANAGED_REGIONS to opt them in."
        )
    return found


def _apply_access_groups(
    db: Session,
    req: ApplyConfigRequest,
    regions: Dict[str, DBRegion],
    changes: List[ApplyChange],
) -> Tuple[Dict[str, DBModelAccessGroup], Dict[int, Set[int]]]:
    """Upsert groups and their region deployments. Returns (slug -> group,
    group_pk -> region ids whose deployment membership changed)."""
    groups: Dict[str, DBModelAccessGroup] = {}
    changed_group_regions: Dict[int, Set[int]] = {}
    for spec in req.access_groups:
        group = db.query(DBModelAccessGroup).filter_by(slug=spec.slug).first()
        if not group:
            group = DBModelAccessGroup(
                slug=spec.slug, label=spec.label, description=spec.description
            )
            db.add(group)
            db.flush()
            changes.append(ApplyChange(entity="access_group", key=spec.slug, action="create"))
        elif group.label != spec.label or group.description != spec.description:
            group.label = spec.label
            group.description = spec.description
            changes.append(
                ApplyChange(entity="access_group", key=spec.slug, action="update", detail="label/description")
            )
        desired_regions = {regions[name].id for name in spec.regions}
        existing_regions = {
            row.region_id
            for row in db.query(DBModelAccessGroupRegion).filter_by(group_id=group.id).all()
        }
        if desired_regions != existing_regions:
            for rid in existing_regions - desired_regions:
                db.query(DBModelAccessGroupRegion).filter_by(
                    group_id=group.id, region_id=rid
                ).delete()
            for rid in desired_regions - existing_regions:
                db.add(DBModelAccessGroupRegion(group_id=group.id, region_id=rid))
            changed_group_regions[group.id] = desired_regions ^ existing_regions
            changes.append(
                ApplyChange(entity="access_group", key=spec.slug, action="update", detail="regions")
            )
        groups[spec.slug] = group
    return groups, changed_group_regions


_CATALOG_FIELDS = (
    "display_name",
    "provider",
    "type",
    "context_length",
    "max_output_tokens",
    "description",
    "real_eol",
    "override_eol",
)


def _tz(value):
    """DB may return naive datetimes (SQLite); compare in UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _upsert_catalog_model(
    db: Session, spec: ApplyModelSpec, changes: List[ApplyChange]
) -> Tuple[DBModel, bool]:
    """Create or update the DBModel row (catalog fields only). Returns
    (model, changed)."""
    model = (
        db.query(DBModel)
        .filter(DBModel.model_id == spec.model_id, DBModel.deleted_at.is_(None))
        .first()
    )
    if model and model.is_alias != spec.is_alias:
        raise _bad_request(
            f"Model '{spec.model_id}' exists with is_alias={model.is_alias}; cannot convert. "
            "Delete it first or pick a new model_id."
        )
    if not model:
        if _contains_sentinel(spec.litellm_params):
            raise _bad_request(
                f"Model '{spec.model_id}' is new but litellm_params contains redacted "
                "placeholder values; provide real credentials or os.environ/ references."
            )
        model = DBModel(
            model_id=spec.model_id,
            is_alias=spec.is_alias,
            is_active_globally=True,
            litellm_params=spec.litellm_params if not spec.is_alias else None,
            **{f: getattr(spec, f) for f in _CATALOG_FIELDS},
        )
        db.add(model)
        db.flush()
        changes.append(ApplyChange(entity="model", key=spec.model_id, action="create"))
        return model, True

    changed_fields = []
    for f in _CATALOG_FIELDS:
        desired = getattr(spec, f)
        if _tz(getattr(model, f)) != _tz(desired):
            setattr(model, f, desired)
            changed_fields.append(f)
    if not model.is_active_globally:
        # Presence in the file means active: reappearing reactivates.
        model.is_active_globally = True
        changed_fields.append("is_active_globally")
    if not spec.is_alias:
        resolved = _merge_credential_sentinels(
            spec.litellm_params or {}, model.litellm_params
        ) or None
        if model.litellm_params != resolved:
            model.litellm_params = resolved
            changed_fields.append("litellm_params")
    if changed_fields:
        changes.append(
            ApplyChange(
                entity="model", key=spec.model_id, action="update", detail=", ".join(changed_fields)
            )
        )
    return model, bool(changed_fields)


def _apply_deployments(
    db: Session,
    model: DBModel,
    spec_key: str,
    desired: Dict[int, Optional[dict]],
    region_names: Dict[int, str],
    changes: List[ApplyChange],
) -> Set[int]:
    """Reconcile DBModelRegion rows to `desired` (region_id -> override).
    Returns region ids needing a sync."""
    to_sync: Set[int] = set()
    existing = {a.region_id: a for a in db.query(DBModelRegion).filter_by(model_id=model.id).all()}
    for region_id, override in desired.items():
        assoc = existing.get(region_id)
        stored = assoc.litellm_params_override if assoc else None
        resolved = _merge_credential_sentinels(override or {}, stored) or None
        if not assoc:
            if _contains_sentinel(override):
                raise _bad_request(
                    f"Deployment '{spec_key}@{region_names[region_id]}' is new but its override "
                    "contains redacted placeholder values; provide real credentials."
                )
            db.add(
                DBModelRegion(
                    model_id=model.id,
                    region_id=region_id,
                    is_active=True,
                    sync_status="pending",
                    litellm_params_override=resolved,
                )
            )
            changes.append(
                ApplyChange(
                    entity="deployment",
                    key=f"{spec_key}@{region_names[region_id]}",
                    action="create",
                )
            )
            to_sync.add(region_id)
            continue
        deployment_changed = []
        if not assoc.is_active:
            assoc.is_active = True
            deployment_changed.append("activated")
        if assoc.litellm_params_override != resolved:
            assoc.litellm_params_override = resolved
            deployment_changed.append("override")
        if deployment_changed:
            assoc.sync_status = "pending"
            assoc.sync_error = None
            assoc.updated_at = datetime.now(UTC)
            changes.append(
                ApplyChange(
                    entity="deployment",
                    key=f"{spec_key}@{region_names[region_id]}",
                    action="update",
                    detail=", ".join(deployment_changed),
                )
            )
            to_sync.add(region_id)
    for region_id, assoc in existing.items():
        if region_id not in desired and assoc.is_active:
            assoc.is_active = False
            assoc.sync_status = "pending"
            assoc.sync_error = None
            assoc.updated_at = datetime.now(UTC)
            changes.append(
                ApplyChange(
                    entity="deployment",
                    key=f"{spec_key}@{region_names.get(region_id, str(region_id))}",
                    action="deactivate",
                )
            )
            to_sync.add(region_id)
    return to_sync


def _replace_group_memberships(
    db: Session, model: DBModel, group_pks: Set[int], changes: List[ApplyChange]
) -> bool:
    existing = {
        row.group_id
        for row in db.query(DBModelAccessGroupModel).filter_by(model_id=model.id).all()
    }
    if existing == group_pks:
        return False
    db.query(DBModelAccessGroupModel).filter_by(model_id=model.id).delete()
    for gid in group_pks:
        db.add(DBModelAccessGroupModel(group_id=gid, model_id=model.id))
    changes.append(
        ApplyChange(entity="model", key=model.model_id, action="update", detail="access_groups")
    )
    return True


@router.post("", response_model=ApplyConfigResponse)
async def apply_model_config(
    req: ApplyConfigRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_role_min_system_admin),
):
    """Apply a full desired-state model config (see module docstring)."""
    _validate_specs(req)
    regions = _resolve_regions(db, req)
    region_names = {r.id: r.name for r in regions.values()}
    # Deactivations can hit regions not referenced in the payload at all.
    for r in db.query(DBRegion.id, DBRegion.name).all():
        region_names.setdefault(r.id, r.name)

    changes: List[ApplyChange] = []
    syncs: Set[Tuple[int, int]] = set()  # (model_pk, region_id)

    groups, changed_group_regions = _apply_access_groups(db, req, regions, changes)

    # Non-alias models first so alias targets can resolve against the DB.
    ordered = [m for m in req.models if not m.is_alias] + [m for m in req.models if m.is_alias]
    models_by_id: Dict[str, DBModel] = {}
    catalog_changed: Set[int] = set()
    for spec in ordered:
        model, changed = _upsert_catalog_model(db, spec, changes)
        models_by_id[spec.model_id] = model
        if changed:
            catalog_changed.add(model.id)

        if spec.is_alias:
            desired_targets = set()
            for t in spec.alias_targets:
                target = models_by_id.get(t.target) or (
                    db.query(DBModel)
                    .filter(DBModel.model_id == t.target, DBModel.deleted_at.is_(None))
                    .first()
                )
                if not target:
                    raise _bad_request(f"Alias '{spec.model_id}': unknown target model '{t.target}'.")
                if target.is_alias:
                    raise _bad_request(
                        f"Alias '{spec.model_id}': target '{t.target}' is an alias; chains are not supported."
                    )
                desired_targets.add((regions[t.region].id, target.id))
            existing_targets = {
                (row.region_id, row.target_model_id)
                for row in db.query(DBModelAliasTarget).filter_by(alias_model_id=model.id).all()
            }
            if desired_targets != existing_targets:
                db.query(DBModelAliasTarget).filter_by(alias_model_id=model.id).delete()
                for region_id, target_pk in desired_targets:
                    db.add(
                        DBModelAliasTarget(
                            alias_model_id=model.id,
                            region_id=region_id,
                            target_model_id=target_pk,
                        )
                    )
                changes.append(
                    ApplyChange(entity="alias_target", key=spec.model_id, action="update")
                )
                catalog_changed.add(model.id)
            desired_deployments: Dict[int, Optional[dict]] = {
                region_id: None for region_id, _ in desired_targets
            }
        else:
            desired_deployments = {
                regions[d.region].id: d.litellm_params_override for d in spec.deployments
            }

        to_sync = _apply_deployments(
            db, model, spec.model_id, desired_deployments, region_names, changes
        )
        group_pks = {groups[slug].id for slug in spec.access_groups}
        if _replace_group_memberships(db, model, group_pks, changes):
            catalog_changed.add(model.id)
        for region_id in to_sync:
            syncs.add((model.id, region_id))

    # Catalog-level changes (params, group membership, alias retarget) must be
    # pushed to every active deployment, not just deployments that changed.
    for spec in ordered:
        model = models_by_id[spec.model_id]
        if model.id in catalog_changed:
            for assoc in db.query(DBModelRegion).filter_by(model_id=model.id).all():
                if assoc.is_active:
                    syncs.add((model.id, assoc.region_id))

    # A group's region set changing re-tags member models in the affected regions.
    for group_pk, affected_regions in changed_group_regions.items():
        member_pks = [
            row.model_id
            for row in db.query(DBModelAccessGroupModel).filter_by(group_id=group_pk).all()
        ]
        if member_pks:
            for assoc in (
                db.query(DBModelRegion)
                .filter(
                    DBModelRegion.model_id.in_(member_pks),
                    DBModelRegion.region_id.in_(affected_regions),
                    DBModelRegion.is_active.is_(True),
                )
                .all()
            ):
                syncs.add((assoc.model_id, assoc.region_id))

    # Models in the DB but absent from the payload.
    payload_ids = {m.model_id for m in req.models}
    unmanaged = (
        db.query(DBModel)
        .filter(DBModel.deleted_at.is_(None), DBModel.model_id.notin_(payload_ids))
        .all()
        if payload_ids
        else db.query(DBModel).filter(DBModel.deleted_at.is_(None)).all()
    )
    unmanaged_models: List[str] = []
    for model in unmanaged:
        if req.prune:
            # Sunset protocol (issue #427): a callable name may only be
            # deactivated after its announced EOL has passed. No EOL = never
            # announced = never pruned. The grace period is the gap between
            # the catalog PR that sets eol_date and the PR that removes the row.
            eol = _tz(model.override_eol) or _tz(model.real_eol)
            if model.is_active_globally and (not eol or eol > datetime.now(UTC)):
                changes.append(
                    ApplyChange(
                        entity="model",
                        key=model.model_id,
                        action="prune_blocked",
                        detail=(
                            f"eol {eol.date()} has not passed" if eol else "no eol_date announced"
                        ),
                    )
                )
                unmanaged_models.append(model.model_id)
                continue
            if model.is_active_globally:
                model.is_active_globally = False
                changes.append(ApplyChange(entity="model", key=model.model_id, action="prune"))
            for assoc in db.query(DBModelRegion).filter_by(model_id=model.id).all():
                if assoc.is_active:
                    assoc.is_active = False
                    assoc.sync_status = "pending"
                    assoc.updated_at = datetime.now(UTC)
                    changes.append(
                        ApplyChange(
                            entity="deployment",
                            key=f"{model.model_id}@{region_names.get(assoc.region_id, assoc.region_id)}",
                            action="deactivate",
                        )
                    )
                    syncs.add((model.id, assoc.region_id))
        else:
            unmanaged_models.append(model.model_id)

    payload_slugs = {g.slug for g in req.access_groups}
    unmanaged_access_groups = sorted(
        row.slug
        for row in db.query(DBModelAccessGroup.slug).filter(
            DBModelAccessGroup.slug.notin_(payload_slugs) if payload_slugs else True
        )
    )

    if req.dry_run:
        db.rollback()
        return ApplyConfigResponse(
            dry_run=True,
            changes=changes,
            unmanaged_models=sorted(unmanaged_models),
            unmanaged_access_groups=unmanaged_access_groups,
            syncs_scheduled=len(syncs),
        )

    db.commit()
    for model_pk, region_id in sorted(syncs):
        background_tasks.add_task(sync_model_to_region_task, model_pk, region_id)
    logger.info(
        f"Applied model config: {len(changes)} changes, {len(syncs)} syncs scheduled"
    )
    return ApplyConfigResponse(
        dry_run=False,
        changes=changes,
        unmanaged_models=sorted(unmanaged_models),
        unmanaged_access_groups=unmanaged_access_groups,
        syncs_scheduled=len(syncs),
    )
