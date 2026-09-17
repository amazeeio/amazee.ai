"""Audit log rows shared by more than one delete path."""

from typing import Optional

from app.db.models import DBAuditLog


def key_delete_audit_log(
    *,
    key_id: int,
    team_id: Optional[int],
    region_id: Optional[int],
    key_name: Optional[str],
    user_id: Optional[int] = None,
    request_source: Optional[str] = None,
    source: Optional[str] = None,
) -> DBAuditLog:
    """Build the audit row for a deleted private AI key.

    The row is added to the caller's session so it shares the delete's
    transaction and cannot outlive a rollback. ``source`` names the automated
    path when no user asked for the delete.
    """
    details = {
        "team_id": team_id,
        "region_id": region_id,
        "key_name": key_name,
    }
    if source is not None:
        details["source"] = source
    return DBAuditLog(
        event_type="private_ai_key.delete",
        resource_type="private_ai_key",
        resource_id=str(key_id),
        action="delete",
        user_id=user_id,
        request_source=request_source,
        details=details,
    )
