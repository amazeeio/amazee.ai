"""add user_spend_cache table

Revision ID: a1b2c3d4e5f6
Revises: 8c3a9f7b2e22
Create Date: 2026-04-03 09:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "8c3a9f7b2e22"


def upgrade() -> None:
    op.create_table(
        "user_spend_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_email", sa.String(), nullable=False),
        sa.Column("response_data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cached_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_email"),
    )
    op.create_index(
        op.f("ix_user_spend_cache_expires_at"),
        "user_spend_cache",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_spend_cache_id"), "user_spend_cache", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_user_spend_cache_normalized_email"),
        "user_spend_cache",
        ["normalized_email"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_spend_cache_normalized_email"), table_name="user_spend_cache"
    )
    op.drop_index(op.f("ix_user_spend_cache_id"), table_name="user_spend_cache")
    op.drop_index(op.f("ix_user_spend_cache_expires_at"), table_name="user_spend_cache")
    op.drop_table("user_spend_cache")
