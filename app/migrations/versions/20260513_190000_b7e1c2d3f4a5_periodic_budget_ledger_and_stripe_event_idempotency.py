"""periodic ledger + stripe event idempotency

Revision ID: b7e1c2d3f4a5
Revises: 2f7c9d1e4aab
Create Date: 2026-05-13 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b7e1c2d3f4a5"
down_revision = "2f7c9d1e4aab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_processed_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stripe_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )
    op.create_index(op.f("ix_stripe_processed_events_id"), "stripe_processed_events", ["id"], unique=False)
    op.create_index(op.f("ix_stripe_processed_events_stripe_event_id"), "stripe_processed_events", ["stripe_event_id"], unique=True)

    op.create_table(
        "periodic_budget_ledger_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(), nullable=False),
        sa.Column("source_payment_id", sa.Integer(), nullable=True),
        sa.Column("source_invoice_id", sa.String(), nullable=True),
        sa.Column("stripe_payment_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("consumed_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_over_from_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_payment_id"], ["periodic_payments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["rolled_over_from_id"], ["periodic_budget_ledger_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "region_id", "entry_type", "stripe_payment_id", name="uq_periodic_ledger_topup_payment"),
    )
    op.create_index(op.f("ix_periodic_budget_ledger_entries_id"), "periodic_budget_ledger_entries", ["id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_team_id"), "periodic_budget_ledger_entries", ["team_id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_region_id"), "periodic_budget_ledger_entries", ["region_id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_entry_type"), "periodic_budget_ledger_entries", ["entry_type"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_source_invoice_id"), "periodic_budget_ledger_entries", ["source_invoice_id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_stripe_payment_id"), "periodic_budget_ledger_entries", ["stripe_payment_id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_expires_at"), "periodic_budget_ledger_entries", ["expires_at"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_rolled_over_from_id"), "periodic_budget_ledger_entries", ["rolled_over_from_id"], unique=False)
    op.create_index(op.f("ix_periodic_budget_ledger_entries_is_active"), "periodic_budget_ledger_entries", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_is_active"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_rolled_over_from_id"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_expires_at"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_stripe_payment_id"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_source_invoice_id"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_entry_type"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_region_id"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_team_id"), table_name="periodic_budget_ledger_entries")
    op.drop_index(op.f("ix_periodic_budget_ledger_entries_id"), table_name="periodic_budget_ledger_entries")
    op.drop_table("periodic_budget_ledger_entries")

    op.drop_index(op.f("ix_stripe_processed_events_stripe_event_id"), table_name="stripe_processed_events")
    op.drop_index(op.f("ix_stripe_processed_events_id"), table_name="stripe_processed_events")
    op.drop_table("stripe_processed_events")
