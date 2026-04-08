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
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default="true"),
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
    op.drop_column("api_tokens", "expiry_option")
    op.drop_column("api_tokens", "expires_at")
    op.drop_index(
        op.f("ix_api_token_expiry_options_slug"), table_name="api_token_expiry_options"
    )
    op.drop_index(
        op.f("ix_api_token_expiry_options_id"), table_name="api_token_expiry_options"
    )
    op.drop_table("api_token_expiry_options")
