"""add_pool_topup_products_catalog

Revision ID: 4d3c2b1a9e8f
Revises: 9f8e7d6c5b4a
Create Date: 2026-03-12 21:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4d3c2b1a9e8f"
down_revision: Union[str, None] = "9f8e7d6c5b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pool_topup_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("stripe_price_id", sa.String(), nullable=False),
        sa.Column("stripe_product_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="usd"
        ),
        sa.Column("region_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_price_id"),
    )
    op.create_index(
        op.f("ix_pool_topup_products_id"), "pool_topup_products", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_pool_topup_products_region_id"),
        "pool_topup_products",
        ["region_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pool_topup_products_stripe_price_id"),
        "pool_topup_products",
        ["stripe_price_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_pool_topup_products_stripe_product_id"),
        "pool_topup_products",
        ["stripe_product_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pool_topup_products_stripe_product_id"),
        table_name="pool_topup_products",
    )
    op.drop_index(
        op.f("ix_pool_topup_products_stripe_price_id"),
        table_name="pool_topup_products",
    )
    op.drop_index(
        op.f("ix_pool_topup_products_region_id"), table_name="pool_topup_products"
    )
    op.drop_index(op.f("ix_pool_topup_products_id"), table_name="pool_topup_products")
    op.drop_table("pool_topup_products")
