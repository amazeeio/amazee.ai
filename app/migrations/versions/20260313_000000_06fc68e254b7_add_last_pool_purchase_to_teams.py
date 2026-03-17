"""add_last_pool_purchase_to_teams

Revision ID: 06fc68e254b7
Revises: e5d0ea6833ff
Create Date: 2026-03-13 00:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic to track migration history
revision: str = "06fc68e254b7"
down_revision: Union[str, None] = "e5d0ea6833ff"


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column("last_pool_purchase", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("teams", "last_pool_purchase")
