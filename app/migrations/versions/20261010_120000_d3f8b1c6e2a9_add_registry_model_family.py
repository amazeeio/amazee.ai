"""add registry model family and manufacturer

Revision ID: d3f8b1c6e2a9
Revises: a7e4c1d9b352
Create Date: 2026-10-10 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d3f8b1c6e2a9"
down_revision = "a7e4c1d9b352"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("registry_models", sa.Column("family", sa.String(), nullable=True))
    op.add_column("registry_models", sa.Column("manufacturer", sa.String(), nullable=True))
    op.create_index(op.f("ix_registry_models_family"), "registry_models", ["family"])


def downgrade() -> None:
    op.drop_index(op.f("ix_registry_models_family"), table_name="registry_models")
    op.drop_column("registry_models", "manufacturer")
    op.drop_column("registry_models", "family")
