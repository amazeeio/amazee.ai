"""add receive_marketing_updates to users

Revision ID: c4a9d8e1f2b3
Revises: 2f7c9d1e4aab
Create Date: 2026-05-20 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4a9d8e1f2b3"
down_revision = "2f7c9d1e4aab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "receive_marketing_updates",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "receive_marketing_updates")
