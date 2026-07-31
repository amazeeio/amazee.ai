"""add budget_alert_state table for budget threshold alerts

Revision ID: d1e2f3a4b5c6
Revises: 6c201dbaea6e
Create Date: 2026-07-29 22:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "6c201dbaea6e"


def upgrade() -> None:
    op.create_table(
        "budget_alert_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("key_id", sa.Integer(), nullable=True),
        sa.Column("period_key", sa.String(), nullable=False),
        sa.Column(
            "last_threshold_pct", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("arm_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("spend_at_notify", sa.Float(), nullable=True),
        sa.Column("budget_at_notify", sa.Float(), nullable=True),
        sa.Column("percent_at_notify", sa.Float(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["key_id"], ["ai_tokens.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_budget_alert_state_id"), "budget_alert_state", ["id"])
    # subject_key is the upsert target for every tick, so it must be unique.
    op.create_index(
        op.f("ix_budget_alert_state_subject_key"),
        "budget_alert_state",
        ["subject_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_budget_alert_state_subject_type"),
        "budget_alert_state",
        ["subject_type"],
    )
    op.create_index(
        op.f("ix_budget_alert_state_region_id"), "budget_alert_state", ["region_id"]
    )
    op.create_index(
        op.f("ix_budget_alert_state_team_id"), "budget_alert_state", ["team_id"]
    )
    op.create_index(
        op.f("ix_budget_alert_state_user_id"), "budget_alert_state", ["user_id"]
    )
    op.create_index(
        op.f("ix_budget_alert_state_key_id"), "budget_alert_state", ["key_id"]
    )
    # The engine loads existing state for a whole region in one query per tick.
    op.create_index(
        "ix_budget_alert_state_region_subject_type",
        "budget_alert_state",
        ["region_id", "subject_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_budget_alert_state_region_subject_type", table_name="budget_alert_state"
    )
    op.drop_index(op.f("ix_budget_alert_state_key_id"), table_name="budget_alert_state")
    op.drop_index(
        op.f("ix_budget_alert_state_user_id"), table_name="budget_alert_state"
    )
    op.drop_index(
        op.f("ix_budget_alert_state_team_id"), table_name="budget_alert_state"
    )
    op.drop_index(
        op.f("ix_budget_alert_state_region_id"), table_name="budget_alert_state"
    )
    op.drop_index(
        op.f("ix_budget_alert_state_subject_type"), table_name="budget_alert_state"
    )
    op.drop_index(
        op.f("ix_budget_alert_state_subject_key"), table_name="budget_alert_state"
    )
    op.drop_index(op.f("ix_budget_alert_state_id"), table_name="budget_alert_state")
    op.drop_table("budget_alert_state")
