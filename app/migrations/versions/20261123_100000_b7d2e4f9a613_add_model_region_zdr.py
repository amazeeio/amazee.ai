"""add zero_data_retention to model_regions

Revision ID: b7d2e4f9a613
Revises: a3e7c5f1d924
Create Date: 2026-11-23 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b7d2e4f9a613"
down_revision = "a3e7c5f1d924"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_regions", sa.Column("zero_data_retention", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_regions", "zero_data_retention")
