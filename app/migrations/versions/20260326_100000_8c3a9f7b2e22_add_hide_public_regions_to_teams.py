"""add hide_public_regions to teams

Revision ID: 8c3a9f7b2e22
Revises: 7f2b1d9c4eaa
Create Date: 2026-03-26 10:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "8c3a9f7b2e22"
down_revision: Union[str, None] = "7f2b1d9c4eaa"


def upgrade() -> None:
    # Add hide_public_regions column to teams table
    op.add_column(
        "teams",
        sa.Column("hide_public_regions", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )


def downgrade() -> None:
    # Remove hide_public_regions column from teams table
    op.drop_column("teams", "hide_public_regions")
