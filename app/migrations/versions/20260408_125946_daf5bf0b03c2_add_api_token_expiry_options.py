"""add_api_token_expiry_options

Revision ID: daf5bf0b03c2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-08 12:59:46.600703+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "daf5bf0b03c2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create api_token_expiry_options table
    op.create_table(
        "api_token_expiry_options",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_api_token_expiry_options_id"),
        "api_token_expiry_options",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_api_token_expiry_options_slug"),
        "api_token_expiry_options",
        ["slug"],
        unique=True,
    )

    # Seed default expiry options so the API is usable immediately after migration
    expiry_options_table = sa.table(
        "api_token_expiry_options",
        sa.column("name", sa.String()),
        sa.column("slug", sa.String()),
        sa.column("days", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        expiry_options_table,
        [
            {"name": "1 day", "slug": "1_day", "days": 1, "is_active": True},
            {"name": "1 week", "slug": "1_week", "days": 7, "is_active": True},
            {"name": "1 month", "slug": "1_month", "days": 30, "is_active": True},
            {"name": "2 months", "slug": "2_months", "days": 60, "is_active": True},
            {"name": "3 months", "slug": "3_months", "days": 90, "is_active": True},
            {"name": "4 months", "slug": "4_months", "days": 120, "is_active": True},
            {"name": "5 months", "slug": "5_months", "days": 150, "is_active": True},
            {"name": "6 months", "slug": "6_months", "days": 180, "is_active": True},
            {"name": "7 months", "slug": "7_months", "days": 210, "is_active": True},
            {"name": "8 months", "slug": "8_months", "days": 240, "is_active": True},
            {"name": "9 months", "slug": "9_months", "days": 270, "is_active": True},
            {"name": "10 months", "slug": "10_months", "days": 300, "is_active": True},
            {"name": "11 months", "slug": "11_months", "days": 330, "is_active": True},
            {"name": "1 year", "slug": "1_year", "days": 365, "is_active": True},
            {"name": "forever", "slug": "forever", "days": None, "is_active": True},
        ],
    )

    # Update api_tokens table - these might already exist in some environments but let's ensure they are there
    # Check if columns exist first to be safe
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("api_tokens")]

    if "expires_at" not in columns:
        op.add_column(
            "api_tokens",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "expiry_option" not in columns:
        op.add_column(
            "api_tokens",
            sa.Column(
                "expiry_option", sa.String(), nullable=False, server_default="forever"
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("api_tokens")]

    if "expiry_option" in columns:
        op.drop_column("api_tokens", "expiry_option")
    if "expires_at" in columns:
        op.drop_column("api_tokens", "expires_at")
    op.drop_index(
        op.f("ix_api_token_expiry_options_slug"), table_name="api_token_expiry_options"
    )
    op.drop_index(
        op.f("ix_api_token_expiry_options_id"), table_name="api_token_expiry_options"
    )
    op.drop_table("api_token_expiry_options")
