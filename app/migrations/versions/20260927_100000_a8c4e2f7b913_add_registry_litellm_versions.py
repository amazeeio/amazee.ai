"""add registry_litellm_versions and registry_model_support.supported

Revision ID: a8c4e2f7b913
Revises: f4b2d8e61a37
Create Date: 2026-09-27 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a8c4e2f7b913"
down_revision = "f4b2d8e61a37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_litellm_versions",
        sa.Column("version", sa.String(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_count", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
    )
    op.add_column("registry_model_support", sa.Column("supported", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("registry_model_support", "supported")
    op.drop_table("registry_litellm_versions")
