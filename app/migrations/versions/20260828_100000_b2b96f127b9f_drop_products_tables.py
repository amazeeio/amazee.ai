"""convert PRODUCT limits to MANUAL and drop the product tables

Revision ID: b2b96f127b9f
Revises: f4c81a9d0b73
Create Date: 2026-08-28 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b2b96f127b9f"
down_revision = "f4c81a9d0b73"
branch_labels = None
depends_on = None

# Marks the rows this migration converted, so the downgrade can put them back.
MIGRATION_SET_BY = "product-table-migration"


def upgrade() -> None:
    # Nothing writes products any more, so PRODUCT-sourced limits have no source
    # to be recomputed from. MANUAL keeps the values and is never overwritten by
    # limit reconciliation, which PRODUCT no longer would be.
    op.execute(
        sa.text(
            """
            UPDATE limited_resources
            SET limited_by = 'MANUAL', set_by = :set_by
            WHERE limited_by = 'PRODUCT'
            """
        ).bindparams(set_by=MIGRATION_SET_BY)
    )

    op.drop_table("team_products")
    op.drop_index(op.f("ix_products_id"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_pricing_tables_table_type"), table_name="pricing_tables")
    op.drop_index(op.f("ix_pricing_tables_id"), table_name="pricing_tables")
    op.drop_table("pricing_tables")


def downgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_count", sa.Integer(), nullable=True),
        sa.Column("keys_per_user", sa.Integer(), nullable=True),
        sa.Column("total_key_count", sa.Integer(), nullable=True),
        sa.Column("service_key_count", sa.Integer(), nullable=True),
        sa.Column("max_budget_per_key", sa.Float(), nullable=True),
        sa.Column("rpm_per_key", sa.Integer(), nullable=True),
        sa.Column("vector_db_count", sa.Integer(), nullable=True),
        sa.Column("vector_db_storage", sa.Integer(), nullable=True),
        sa.Column("renewal_period_days", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_products_id"), "products", ["id"], unique=False)

    op.create_table(
        "team_products",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "product_id"),
    )

    op.create_table(
        "pricing_tables",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("table_type", sa.String(), nullable=False),
        sa.Column("pricing_table_id", sa.String(), nullable=False),
        sa.Column("stripe_publishable_key", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "table_type", "is_active", name="uq_pricing_table_type_active"
        ),
    )
    op.create_index(
        op.f("ix_pricing_tables_id"), "pricing_tables", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_pricing_tables_table_type"),
        "pricing_tables",
        ["table_type"],
        unique=False,
    )

    # Table rows are gone for good; only the limit source can be restored.
    op.execute(
        sa.text(
            """
            UPDATE limited_resources
            SET limited_by = 'PRODUCT', set_by = NULL
            WHERE limited_by = 'MANUAL' AND set_by = :set_by
            """
        ).bindparams(set_by=MIGRATION_SET_BY)
    )
