"""add model_regions.access_groups_override

Revision ID: f2c5a0821a29
Revises: b2b96f127b9f
Create Date: 2026-09-16 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "f2c5a0821a29"
down_revision = "b2b96f127b9f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_regions", sa.Column("access_groups_override", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_regions", "access_groups_override")
