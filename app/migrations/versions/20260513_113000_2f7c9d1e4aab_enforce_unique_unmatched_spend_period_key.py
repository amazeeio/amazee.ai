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


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        indexes = insp.get_indexes(table_name)
    except sa.exc.NoSuchTableError:
        return False
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    bind = op.get_bind()
    index_name = "uq_team_spend_period_key_null_key_id"
    if not _index_exists(bind, "team_spend_period_keys", index_name):
        op.create_index(
            index_name,
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
        if_exists=True,
    )
