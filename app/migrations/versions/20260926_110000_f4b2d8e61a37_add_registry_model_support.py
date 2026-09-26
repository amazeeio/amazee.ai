"""add registry_model_support

Revision ID: f4b2d8e61a37
Revises: e1a7c3b95d20
Create Date: 2026-09-26 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f4b2d8e61a37"
down_revision = "e1a7c3b95d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_model_support",
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("registry_models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("priced", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("registry_model_support")
