"""add registry events

Revision ID: f1b6d3a8c527
Revises: e7a2c4f9b815
Create Date: 2026-10-10 14:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f1b6d3a8c527"
down_revision = "e7a2c4f9b815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("registry_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index(op.f("ix_registry_events_type"), "registry_events", ["type"])
    op.create_index(op.f("ix_registry_events_model_id"), "registry_events", ["model_id"])
    op.create_index(op.f("ix_registry_events_created_at"), "registry_events", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_registry_events_created_at"), table_name="registry_events")
    op.drop_index(op.f("ix_registry_events_model_id"), table_name="registry_events")
    op.drop_index(op.f("ix_registry_events_type"), table_name="registry_events")
    op.drop_table("registry_events")
