"""periodic ledger partial unique indexes for nullable invoice ids

Revision ID: 6e9b1c2d4f8a
Revises: 4d2a1b9c8e7f
Create Date: 2026-05-15 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "6e9b1c2d4f8a"
down_revision = "4d2a1b9c8e7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_periodic_ledger_subscription_invoice",
        "periodic_budget_ledger_entries",
        type_="unique",
    )
    op.drop_constraint(
        "uq_periodic_ledger_rollover_source_invoice",
        "periodic_budget_ledger_entries",
        type_="unique",
    )

    op.create_index(
        "uq_periodic_ledger_subscription_invoice_not_null",
        "periodic_budget_ledger_entries",
        ["team_id", "region_id", "entry_type", "source_invoice_id"],
        unique=True,
        postgresql_where=sa.text("source_invoice_id IS NOT NULL"),
    )
    op.create_index(
        "uq_periodic_ledger_rollover_source_invoice_not_null",
        "periodic_budget_ledger_entries",
        ["rolled_over_from_id", "source_invoice_id"],
        unique=True,
        postgresql_where=sa.text("source_invoice_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_periodic_ledger_rollover_source_invoice_not_null",
        table_name="periodic_budget_ledger_entries",
    )
    op.drop_index(
        "uq_periodic_ledger_subscription_invoice_not_null",
        table_name="periodic_budget_ledger_entries",
    )

    op.create_unique_constraint(
        "uq_periodic_ledger_subscription_invoice",
        "periodic_budget_ledger_entries",
        ["team_id", "region_id", "entry_type", "source_invoice_id"],
    )
    op.create_unique_constraint(
        "uq_periodic_ledger_rollover_source_invoice",
        "periodic_budget_ledger_entries",
        ["rolled_over_from_id", "source_invoice_id"],
    )
