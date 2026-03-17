"""repair_pool_purchases_drift

Revision ID: 7f2b1d9c4eaa
Revises: 3c1a9f7b2d11
Create Date: 2026-03-17 13:45:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f2b1d9c4eaa"
down_revision: Union[str, None] = "3c1a9f7b2d11"


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        indexes = insp.get_indexes(table_name)
    except Exception:
        return False
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "pool_purchases"):
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
            sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists(bind, "pool_purchases", "ix_pool_purchases_team_id"):
        op.create_index("ix_pool_purchases_team_id", "pool_purchases", ["team_id"])

    if not _index_exists(bind, "pool_purchases", "ix_pool_purchases_region_id"):
        op.create_index("ix_pool_purchases_region_id", "pool_purchases", ["region_id"])

    if not _index_exists(bind, "pool_purchases", "ix_pool_purchases_stripe_payment_id"):
        op.create_index(
            "ix_pool_purchases_stripe_payment_id",
            "pool_purchases",
            ["stripe_payment_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "pool_purchases"):
        if _index_exists(bind, "pool_purchases", "ix_pool_purchases_stripe_payment_id"):
            op.drop_index("ix_pool_purchases_stripe_payment_id", table_name="pool_purchases")
        if _index_exists(bind, "pool_purchases", "ix_pool_purchases_region_id"):
            op.drop_index("ix_pool_purchases_region_id", table_name="pool_purchases")
        if _index_exists(bind, "pool_purchases", "ix_pool_purchases_team_id"):
            op.drop_index("ix_pool_purchases_team_id", table_name="pool_purchases")
        op.drop_table("pool_purchases")
