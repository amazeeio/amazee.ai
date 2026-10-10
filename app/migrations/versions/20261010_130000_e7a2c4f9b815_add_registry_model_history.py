"""add registry model history

Revision ID: e7a2c4f9b815
Revises: d3f8b1c6e2a9
Create Date: 2026-10-10 13:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e7a2c4f9b815"
down_revision = "d3f8b1c6e2a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_model_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("registry_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_registry_model_history_model_id_recorded_at",
        "registry_model_history",
        ["model_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_registry_model_history_model_id_recorded_at", table_name="registry_model_history")
    op.drop_table("registry_model_history")
