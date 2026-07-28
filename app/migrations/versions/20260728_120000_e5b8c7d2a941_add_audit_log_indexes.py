"""add indexes to audit_logs

The audit_logs table has no indexes beyond the primary key, so the admin
Audit Logs page (ORDER BY timestamp, count(), and the three DISTINCT
metadata queries) does full-table scans on every load and times out on
large production tables.

Indexes are created CONCURRENTLY because audit_logs receives an insert on
every API request; a blocking build would stall all traffic for the
duration. if_not_exists makes a retry after a failed concurrent build a
no-op instead of an error (drop any INVALID index manually before
retrying).

Revision ID: e5b8c7d2a941
Revises: 6c201dbaea6e
Create Date: 2026-07-28 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e5b8c7d2a941"
down_revision: Union[str, None] = "6c201dbaea6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEXES = [
    ("ix_audit_logs_timestamp", ["timestamp"]),
    ("ix_audit_logs_event_type", ["event_type"]),
    ("ix_audit_logs_resource_type", ["resource_type"]),
    ("ix_audit_logs_user_id", ["user_id"]),
    # Expression index matching details ->> 'status_code' as emitted by
    # DBAuditLog.details["status_code"].as_string() in app/api/audit.py.
    ("ix_audit_logs_status_code", [sa.text("(details ->> 'status_code')")]),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, columns in INDEXES:
            op.create_index(
                name,
                "audit_logs",
                columns,
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _ in INDEXES:
            op.drop_index(
                name,
                table_name="audit_logs",
                postgresql_concurrently=True,
                if_exists=True,
            )
