"""add is_public to model_access_groups

Revision ID: d8e3f1a6c2b4
Revises: c4d7e2a91b30
Create Date: 2026-09-23 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d8e3f1a6c2b4"
down_revision = "c4d7e2a91b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_access_groups",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("model_access_groups", "is_public")
