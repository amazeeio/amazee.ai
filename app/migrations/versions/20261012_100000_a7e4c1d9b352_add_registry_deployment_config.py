"""add registry deployment config

Revision ID: a7e4c1d9b352
Revises: c9e3a7d5f041
Create Date: 2026-10-12 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a7e4c1d9b352"
down_revision = "c9e3a7d5f041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No backfill: the proxy gives this config only at import time.
    for column in ("litellm_params", "model_info"):
        op.add_column(
            "registry_model_regions",
            sa.Column(column, sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade() -> None:
    op.drop_column("registry_model_regions", "model_info")
    op.drop_column("registry_model_regions", "litellm_params")
