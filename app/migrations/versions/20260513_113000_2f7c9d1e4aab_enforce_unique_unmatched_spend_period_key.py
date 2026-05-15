"""enforce unique unmatched spend period key rows

Revision ID: 2f7c9d1e4aab
Revises: 9d1a2b3c4d5e
Create Date: 2026-05-13 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2f7c9d1e4aab"
down_revision = "9d1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_team_spend_period_key_null_key_id",
        "team_spend_period_keys",
        ["team_spend_period_id", "owner_id", "key_name_snapshot"],
        unique=True,
        postgresql_where=sa.text("key_id IS NULL"),
        sqlite_where=sa.text("key_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_team_spend_period_key_null_key_id",
        table_name="team_spend_period_keys",
    )
