"""periodic ledger uniqueness hardening

Revision ID: 4d2a1b9c8e7f
Revises: b7e1c2d3f4a5
Create Date: 2026-05-13 21:30:00.000000
"""

from alembic import op


revision = "4d2a1b9c8e7f"
down_revision = "b7e1c2d3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_constraint(
        "uq_periodic_ledger_rollover_source_invoice",
        "periodic_budget_ledger_entries",
        type_="unique",
    )
    op.drop_constraint(
        "uq_periodic_ledger_subscription_invoice",
        "periodic_budget_ledger_entries",
        type_="unique",
    )
