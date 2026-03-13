"""add_pool_purchases_table

Revision ID: e5d0ea6833ff
Revises: 95ac6f88662f
Create Date: 2026-03-13 00:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic to track migration history
revision: str = "e5d0ea6833ff"  # noqa: F841
down_revision: Union[str, None] = "95ac6f88662f"  # noqa: F841


def upgrade() -> None:
    op.create_table(
        "pool_purchases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stripe_payment_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("stripe_payment_id"),
    )
    op.create_index("ix_pool_purchases_team_id", "pool_purchases", ["team_id"])
    op.create_index("ix_pool_purchases_region_id", "pool_purchases", ["region_id"])
    op.create_index(
        "ix_pool_purchases_stripe_payment_id",
        "pool_purchases",
        ["stripe_payment_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_pool_purchases_stripe_payment_id", table_name="pool_purchases")
    op.drop_index("ix_pool_purchases_region_id", table_name="pool_purchases")
    op.drop_index("ix_pool_purchases_team_id", table_name="pool_purchases")
    op.drop_table("pool_purchases")
