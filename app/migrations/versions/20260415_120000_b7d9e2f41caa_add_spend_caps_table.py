"""add spend_caps table

Revision ID: b7d9e2f41caa
Revises: a1b2c3d4e5f6
Create Date: 2026-04-15 12:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7d9e2f41caa"
down_revision: Union[str, None] = "a1b2c3d4e5f6"


def upgrade() -> None:
    op.create_table(
        "spend_caps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("key_id", sa.Integer(), nullable=True),
        sa.Column("max_budget", sa.Float(), nullable=True),
        sa.Column("budget_duration", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["key_id"], ["ai_tokens.id"]),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_spend_caps_id"), "spend_caps", ["id"], unique=False)
    op.create_index(op.f("ix_spend_caps_scope"), "spend_caps", ["scope"], unique=False)
    op.create_index(
        op.f("ix_spend_caps_region_id"), "spend_caps", ["region_id"], unique=False
    )
    op.create_index(
        op.f("ix_spend_caps_team_id"), "spend_caps", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_spend_caps_user_id"), "spend_caps", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_spend_caps_key_id"), "spend_caps", ["key_id"], unique=False
    )
    op.create_index(
        "uq_spend_caps_team_scope",
        "spend_caps",
        ["region_id", "team_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'team' AND team_id IS NOT NULL AND user_id IS NULL AND key_id IS NULL"
        ),
    )
    op.create_index(
        "uq_spend_caps_team_member_scope",
        "spend_caps",
        ["region_id", "team_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'team_member' AND team_id IS NOT NULL AND user_id IS NOT NULL AND key_id IS NULL"
        ),
    )
    op.create_index(
        "uq_spend_caps_key_scope",
        "spend_caps",
        ["region_id", "key_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'key' AND key_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_spend_caps_key_scope", table_name="spend_caps")
    op.drop_index("uq_spend_caps_team_member_scope", table_name="spend_caps")
    op.drop_index("uq_spend_caps_team_scope", table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_key_id"), table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_user_id"), table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_team_id"), table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_region_id"), table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_scope"), table_name="spend_caps")
    op.drop_index(op.f("ix_spend_caps_id"), table_name="spend_caps")
    op.drop_table("spend_caps")
