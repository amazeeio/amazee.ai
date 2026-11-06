"""add registry model overrides

Revision ID: b8d2e5f1c374
Revises: f1b6d3a8c527
Create Date: 2026-10-15 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b8d2e5f1c374"
down_revision = "f1b6d3a8c527"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("registry_models", sa.Column("override_eol", sa.Date(), nullable=True))
    op.add_column(
        "registry_models",
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("registry_models", "blocked")
    op.drop_column("registry_models", "override_eol")
