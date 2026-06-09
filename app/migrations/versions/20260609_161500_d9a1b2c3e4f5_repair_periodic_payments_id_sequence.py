"""repair periodic_payments id sequence/default drift

Revision ID: d9a1b2c3e4f5
Revises: 8b7c6d5e4f3a
Create Date: 2026-06-09 16:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d9a1b2c3e4f5"
down_revision = "8b7c6d5e4f3a"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _table_exists(bind, "periodic_payments"):
        return

    op.execute("CREATE SEQUENCE IF NOT EXISTS periodic_payments_id_seq START WITH 1")
    op.execute(
        "ALTER TABLE periodic_payments "
        "ALTER COLUMN id SET DEFAULT nextval('periodic_payments_id_seq')"
    )
    op.execute("ALTER SEQUENCE periodic_payments_id_seq OWNED BY periodic_payments.id")
    op.execute(
        "SELECT setval('periodic_payments_id_seq', "
        "COALESCE((SELECT MAX(id) FROM periodic_payments), 0), true)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not _table_exists(bind, "periodic_payments"):
        return

    op.execute("ALTER TABLE periodic_payments ALTER COLUMN id DROP DEFAULT")
    op.execute("DROP SEQUENCE IF EXISTS periodic_payments_id_seq")
