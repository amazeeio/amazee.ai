from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import Optional
from datetime import datetime, UTC
from sqlalchemy import distinct, or_, cast, String
from sqlalchemy.exc import DBAPIError
from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import (
    AuditLogResponse,
    PaginatedAuditLogResponse,
    AuditLogMetadata,
)
from app.db.models import DBAuditLog, DBUser
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audit"])


def _as_naive_utc(dt: datetime) -> datetime:
    # DBAuditLog.timestamp is stored as naive UTC; normalize tz-aware
    # query params so comparisons don't depend on the server timezone.
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


# Renders as (details ->> 'status_code') on PostgreSQL; must match the
# expression index ix_audit_logs_status_code.
_status_code_expr = DBAuditLog.details["status_code"].as_string()


def _status_code_by_id_without_json_decode(db: Session) -> dict[int, str | None]:
    """Fallback for ``_status_code_expr``: {audit_log.id: status_code}.

    A historical row can carry a null-byte unicode escape sequence inside a
    details string (e.g. a path-traversal probe payload). Postgres accepts
    that into a `json` column at write time (it only validates the text, it
    does not decode it), but `->>` decodes the string and rejects the null
    byte - failing the whole query, not just the offending row. Casting to
    text instead of extracting a field never asks Postgres to decode the
    escape (a json->text cast returns the stored text unchanged), so it
    can't fail; the decoding happens in Python instead, where a null byte is
    just another character - unlike a text-based substring replace, this
    can't corrupt an unrelated, legitimately escaped backslash elsewhere in
    the same row. New rows never carry this escape - see
    app/middleware/audit.py:_strip_null_bytes - so this is only needed for
    rows written before that fix.
    """
    result: dict[int, str | None] = {}
    for row_id, raw in db.query(DBAuditLog.id, cast(DBAuditLog.details, String)).all():
        code = None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                value = parsed.get("status_code")
                code = None if value is None else str(value)
        result[row_id] = code
    return result


@router.get("/logs", response_model=PaginatedAuditLogResponse)
async def get_audit_logs(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user_from_auth),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    event_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    user_id: Optional[int] = None,
    user_email: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    status_code: Optional[str] = None,
    referer: Optional[str] = None,
):
    """
    Retrieve audit logs with optional filtering.
    Only accessible by admin users.
    event_type, resource_type, and status_code can be comma-separated lists for multiple values.
    referer matches as a substring against both the referer and origin columns.
    """
    if not current_user.is_admin:
        logger.warning(
            f"Non-admin user {current_user.id} attempted to access audit logs"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access audit logs",
        )

    try:
        query = db.query(DBAuditLog)
        # Only join users when filtering by email; an unconditional join
        # makes the count() below scan far more than it needs to.
        if user_email:
            query = query.join(DBUser, DBAuditLog.user_id == DBUser.id).filter(
                DBUser.email.ilike(f"%{user_email}%")
            )

        if event_type:
            event_types = [et.strip() for et in event_type.split(",")]
            query = query.filter(DBAuditLog.event_type.in_(event_types))
        if resource_type:
            resource_types = [rt.strip() for rt in resource_type.split(",")]
            query = query.filter(DBAuditLog.resource_type.in_(resource_types))
        if user_id:
            query = query.filter(DBAuditLog.user_id == user_id)
        if from_date:
            query = query.filter(DBAuditLog.timestamp >= _as_naive_utc(from_date))
        if to_date:
            query = query.filter(DBAuditLog.timestamp <= _as_naive_utc(to_date))
        if referer:
            # Escape LIKE wildcards so a literal % or _ in the search term
            # doesn't act as a match-all pattern.
            escaped = (
                referer.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            # ponytail: seq scan; add a pg_trgm index if audit table growth makes this slow
            query = query.filter(
                or_(
                    DBAuditLog.referer.ilike(f"%{escaped}%", escape="\\"),
                    DBAuditLog.origin.ilike(f"%{escaped}%", escape="\\"),
                )
            )

        status_codes = (
            [sc.strip() for sc in status_code.split(",")] if status_code else None
        )
        filtered_query = (
            query.filter(_status_code_expr.in_(status_codes))
            if status_codes
            else query
        )

        try:
            total = filtered_query.count()
            # Execute the query with pagination; eager-load user to avoid a
            # lazy-load query per row when building the response.
            results = (
                filtered_query.options(joinedload(DBAuditLog.user))
                .order_by(DBAuditLog.timestamp.desc())
                .offset(skip)
                .limit(limit)
                .all()
            )
        except DBAPIError:
            if not status_codes:
                raise
            # A historical row's details can't be decoded by the `->>` in
            # _status_code_expr (see _status_code_by_id_without_json_decode);
            # fall back to an id-based filter that never asks Postgres to
            # decode it. The transaction is aborted after the failed query
            # and must be rolled back before it can run another one.
            db.rollback()
            wanted = set(status_codes)
            matching_ids = [
                row_id
                for row_id, code in _status_code_by_id_without_json_decode(db).items()
                if code in wanted
            ]
            filtered_query = query.filter(DBAuditLog.id.in_(matching_ids))
            total = filtered_query.count()
            results = (
                filtered_query.options(joinedload(DBAuditLog.user))
                .order_by(DBAuditLog.timestamp.desc())
                .offset(skip)
                .limit(limit)
                .all()
            )

        response_data = [
            AuditLogResponse(
                id=log.id,
                timestamp=log.timestamp,
                user_id=log.user_id,
                user_email=log.user.email if log.user else None,
                event_type=log.event_type,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                action=log.action,
                details=log.details,
                ip_address=log.ip_address,
                user_agent=log.user_agent,
                request_source=log.request_source,
                referer=log.referer,
                origin=log.origin,
            )
            for log in results
        ]

        return {"items": response_data, "total": total}

    except Exception as e:
        logger.error(f"Error fetching audit logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching audit logs: {str(e)}",
        )


@router.get("/logs/metadata", response_model=AuditLogMetadata)
async def get_audit_logs_metadata(
    db: Session = Depends(get_db),
    current_user: DBUser = Depends(get_current_user_from_auth),
):
    """
    Retrieve distinct event types, resource types, and status codes from audit logs.
    Only accessible by admin users.
    """
    if not current_user.is_admin:
        logger.warning(
            f"Non-admin user {current_user.id} attempted to access audit logs metadata"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access audit logs metadata",
        )

    try:
        # Get distinct event types and resource types, filtering out None and empty strings
        event_types = [
            et[0]
            for et in db.query(distinct(DBAuditLog.event_type))
            .filter(DBAuditLog.event_type.isnot(None))
            .filter(DBAuditLog.event_type != "")
            .all()
        ]
        resource_types = [
            rt[0]
            for rt in db.query(distinct(DBAuditLog.resource_type))
            .filter(DBAuditLog.resource_type.isnot(None))
            .filter(DBAuditLog.resource_type != "")
            .all()
        ]

        # Get distinct status codes from the details JSON field
        try:
            status_codes = [
                sc[0]
                for sc in db.query(distinct(_status_code_expr))
                .filter(_status_code_expr.isnot(None))
                .filter(_status_code_expr != "")
                .all()
            ]
        except DBAPIError:
            # A historical row's details can't be decoded by the `->>` in
            # _status_code_expr (see _status_code_by_id_without_json_decode).
            # The transaction is aborted after the failed query and must be
            # rolled back before it can run another one.
            db.rollback()
            status_codes = sorted(
                {
                    code
                    for code in _status_code_by_id_without_json_decode(db).values()
                    if code
                }
            )

        return {
            "event_types": sorted(event_types),
            "resource_types": sorted(resource_types),
            "status_codes": sorted(
                status_codes, key=lambda x: int(x) if x.isdigit() else 0
            ),
        }

    except Exception as e:
        logger.error(f"Error fetching audit logs metadata: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching audit logs metadata: {str(e)}",
        )
