"""add model_info to models and model_info_override to model_regions

Revision ID: a3f9c6e1d7b2
Revises: d8e3f1a6c2b4
Create Date: 2026-09-24 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a3f9c6e1d7b2"
down_revision = "d8e3f1a6c2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("models", sa.Column("model_info", sa.JSON(), nullable=True))
    op.add_column("model_regions", sa.Column("model_info_override", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_regions", "model_info_override")
    op.drop_column("models", "model_info")
