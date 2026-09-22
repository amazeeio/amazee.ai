"""add manufacturer_name / manufacturer_website to models

Revision ID: c4d7e2a91b30
Revises: f2c5a0821a29
Create Date: 2026-09-22 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c4d7e2a91b30"
down_revision = "f2c5a0821a29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("models", sa.Column("manufacturer_name", sa.String(), nullable=True))
    op.add_column(
        "models", sa.Column("manufacturer_website", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("models", "manufacturer_website")
    op.drop_column("models", "manufacturer_name")
