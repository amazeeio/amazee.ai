from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from sqlalchemy import distinct, text, cast, String
from app.db.database import get_db
from app.api.auth import get_current_user_from_auth
from app.schemas.models import AuditLogResponse, PaginatedAuditLogResponse, AuditLogMetadata
from app.db.models import DBAuditLog, DBUser
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["audit"])

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
):
    """
    Retrieve audit logs with optional filtering.
    Only accessible by admin users.
    event_type, resource_type, and status_code can be comma-separated lists for multiple values.
    """
    if not current_user.is_admin:
        logger.warning(f"Non-admin user {current_user.id} attempted to access audit logs")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access audit logs"
        )

    try:
        # Use the correct model
        query = db.query(DBAuditLog).outerjoin(DBUser, DBAuditLog.user_id == DBUser.id)

        if event_type:
            event_types = [et.strip() for et in event_type.split(',')]
            query = query.filter(DBAuditLog.event_type.in_(event_types))
        if resource_type:
            resource_types = [rt.strip() for rt in resource_type.split(',')]
            query = query.filter(DBAuditLog.resource_type.in_(resource_types))
        if user_id:
            query = query.filter(DBAuditLog.user_id == user_id)
        if user_email:
            query = query.filter(DBUser.email.ilike(f"%{user_email}%"))
        if from_date:
            query = query.filter(DBAuditLog.timestamp >= from_date)
        if to_date:
            query = query.filter(DBAuditLog.timestamp <= to_date)
        if status_code:
            status_codes = [sc.strip() for sc in status_code.split(',')]
            query = query.filter(cast(DBAuditLog.details['status_code'], String).in_(status_codes))

        # Get total count
        total = query.count()

        # Execute the query with pagination
        results = query.order_by(DBAuditLog.timestamp.desc()).offset(skip).limit(limit).all()

        response_data = [AuditLogResponse(
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
            request_source=log.request_source
        ) for log in results]

        return {
            "items": response_data,
            "total": total
        }

    except Exception as e:
        logger.error(f"Error fetching audit logs: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching audit logs: {str(e)}"
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
        logger.warning(f"Non-admin user {current_user.id} attempted to access audit logs metadata")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access audit logs metadata"
        )

    try:
        # Get distinct event types and resource types, filtering out None and empty strings
        event_types = [et[0] for et in db.query(distinct(DBAuditLog.event_type))
                      .filter(DBAuditLog.event_type.isnot(None))
                      .filter(DBAuditLog.event_type != '')
                      .all()]
        resource_types = [rt[0] for rt in db.query(distinct(DBAuditLog.resource_type))
                        .filter(DBAuditLog.resource_type.isnot(None))
                        .filter(DBAuditLog.resource_type != '')
                        .all()]

        # Get distinct status codes from the details JSON field
        status_codes = [sc[0] for sc in db.query(distinct(cast(DBAuditLog.details['status_code'], String)))
                       .filter(cast(DBAuditLog.details['status_code'], String).isnot(None))
                       .filter(cast(DBAuditLog.details['status_code'], String) != '')
                       .all()]

        return {
            "event_types": sorted(event_types),
            "resource_types": sorted(resource_types),
            "status_codes": sorted(status_codes, key=lambda x: int(x) if x.isdigit() else 0)
        }

    except Exception as e:
        logger.error(f"Error fetching audit logs metadata: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching audit logs metadata: {str(e)}"
        )