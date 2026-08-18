"""drop ineffective rollover unique index using nullable foreign key

Revision ID: c1d2e3f4a5b6
Revises: f0a1b2c3d4e5
Create Date: 2026-05-28 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "uq_periodic_ledger_rollover_source_invoice_not_null",
        table_name="periodic_budget_ledger_entries",
    )


def downgrade() -> None:
    op.create_index(
        "uq_periodic_ledger_rollover_source_invoice_not_null",
        "periodic_budget_ledger_entries",
        ["rolled_over_from_id", "source_invoice_id"],
        unique=True,
        postgresql_where=sa.text("source_invoice_id IS NOT NULL"),
    )
