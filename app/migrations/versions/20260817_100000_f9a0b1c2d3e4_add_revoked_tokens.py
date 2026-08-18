"""add revoked_tokens table so logout can invalidate an access token

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-08-17 10:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
    )
    op.create_index(op.f("ix_revoked_tokens_id"), "revoked_tokens", ["id"])
    # The hot path: one lookup by jti on every authenticated request.
    op.create_index(op.f("ix_revoked_tokens_jti"), "revoked_tokens", ["jti"])
    op.create_index(op.f("ix_revoked_tokens_user_id"), "revoked_tokens", ["user_id"])
    op.create_index(
        op.f("ix_revoked_tokens_expires_at"), "revoked_tokens", ["expires_at"]
    )
    op.create_index(
        op.f("ix_revoked_tokens_revoked_at"), "revoked_tokens", ["revoked_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_revoked_tokens_revoked_at"), table_name="revoked_tokens")
    op.drop_index(op.f("ix_revoked_tokens_expires_at"), table_name="revoked_tokens")
    op.drop_index(op.f("ix_revoked_tokens_user_id"), table_name="revoked_tokens")
    op.drop_index(op.f("ix_revoked_tokens_jti"), table_name="revoked_tokens")
    op.drop_index(op.f("ix_revoked_tokens_id"), table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
