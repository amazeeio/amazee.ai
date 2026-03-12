"""pool_budget_mode_foundation

Revision ID: b7e4d90f1c2a
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-12 10:45:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7e4d90f1c2a"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "budget_mode", sa.String(), nullable=False, server_default="periodic"
        ),
    )

    op.add_column(
        "team_regions",
        sa.Column("last_budget_purchase_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "team_regions",
        sa.Column(
            "aggregate_spend_cents", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "team_regions",
        sa.Column(
            "total_budget_purchased_cents",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "team_regions",
        sa.Column("last_spend_synced_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "budget_purchases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("stripe_session_id", sa.String(), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(), nullable=True),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="usd"
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("previous_budget_cents", sa.BigInteger(), nullable=False),
        sa.Column("new_budget_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "purchased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_session_id"),
    )
    op.create_index(
        op.f("ix_budget_purchases_id"), "budget_purchases", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_budget_purchases_region_id"),
        "budget_purchases",
        ["region_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_budget_purchases_team_id"),
        "budget_purchases",
        ["team_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_budget_purchases_stripe_payment_intent_id"),
        "budget_purchases",
        ["stripe_payment_intent_id"],
        unique=False,
    )
    op.create_index(
        "ix_budget_purchases_team_region_purchased_at",
        "budget_purchases",
        ["team_id", "region_id", "purchased_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_budget_purchases_team_region_purchased_at", table_name="budget_purchases"
    )
    op.drop_index(
        op.f("ix_budget_purchases_stripe_payment_intent_id"),
        table_name="budget_purchases",
    )
    op.drop_index(op.f("ix_budget_purchases_team_id"), table_name="budget_purchases")
    op.drop_index(op.f("ix_budget_purchases_region_id"), table_name="budget_purchases")
    op.drop_index(op.f("ix_budget_purchases_id"), table_name="budget_purchases")
    op.drop_table("budget_purchases")

    op.drop_column("team_regions", "last_spend_synced_at")
    op.drop_column("team_regions", "total_budget_purchased_cents")
    op.drop_column("team_regions", "aggregate_spend_cents")
    op.drop_column("team_regions", "last_budget_purchase_at")

    op.drop_column("teams", "budget_mode")
