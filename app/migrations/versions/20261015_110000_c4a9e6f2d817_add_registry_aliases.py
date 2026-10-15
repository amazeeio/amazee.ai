"""add registry aliases

Revision ID: c4a9e6f2d817
Revises: b8d2e5f1c374
Create Date: 2026-10-15 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c4a9e6f2d817"
down_revision = "b8d2e5f1c374"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("region_id", "alias"),
    )


def downgrade() -> None:
    op.drop_table("registry_aliases")
