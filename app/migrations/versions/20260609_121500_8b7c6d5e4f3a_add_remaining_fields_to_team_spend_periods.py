"""add remaining fields to team_spend_periods

Revision ID: 8b7c6d5e4f3a
Revises: c1d2e3f4a5b6
Create Date: 2026-06-09 12:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "8b7c6d5e4f3a"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_spend_periods",
        sa.Column("subscription_remaining_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "team_spend_periods",
        sa.Column("topup_remaining_cents", sa.Integer(), nullable=True),
    )
    op.add_column(
        "team_spend_periods",
        sa.Column("desired_remaining_cents", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_spend_periods", "desired_remaining_cents")
    op.drop_column("team_spend_periods", "topup_remaining_cents")
    op.drop_column("team_spend_periods", "subscription_remaining_cents")
