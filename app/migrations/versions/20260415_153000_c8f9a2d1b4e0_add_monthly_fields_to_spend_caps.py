"""add monthly fields to spend_caps

Revision ID: c8f9a2d1b4e0
Revises: b7d9e2f41caa
Create Date: 2026-04-15 15:30:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f9a2d1b4e0"
down_revision: Union[str, None] = "b7d9e2f41caa"


def upgrade() -> None:
    op.add_column("spend_caps", sa.Column("month_anchor", sa.Date(), nullable=True))
    op.add_column(
        "spend_caps", sa.Column("month_start_spend", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("spend_caps", "month_start_spend")
    op.drop_column("spend_caps", "month_anchor")
