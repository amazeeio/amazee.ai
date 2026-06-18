"""add created_via_drupal to users

Revision ID: a1b2c3d4e5f6
Revises: d9a1b2c3e4f5
Create Date: 2026-06-18 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "d9a1b2c3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "created_via_drupal",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "created_via_drupal")
