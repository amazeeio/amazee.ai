"""add require_purchase_for_requests to teams

Revision ID: f1c2d3e4a5b6
Revises: c8f9a2d1b4e0
Create Date: 2026-04-29 06:10:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1c2d3e4a5b6"
down_revision: Union[str, None] = "c8f9a2d1b4e0"


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "require_purchase_for_requests",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("teams", "require_purchase_for_requests")
