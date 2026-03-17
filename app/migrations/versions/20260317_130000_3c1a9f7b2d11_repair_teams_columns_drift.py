"""repair_teams_columns_drift

Revision ID: 3c1a9f7b2d11
Revises: 06fc68e254b7
Create Date: 2026-03-17 13:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "3c1a9f7b2d11"
down_revision: Union[str, None] = "06fc68e254b7"


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        cols = insp.get_columns(table_name)
    except Exception:
        return False
    return any(col["name"] == column_name for col in cols)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'budget_type_enum') THEN
                CREATE TYPE budget_type_enum AS ENUM ('periodic', 'pool');
            END IF;
        END $$;
        """
    )

    if not _column_exists(bind, "teams", "budget_type"):
        op.add_column(
            "teams",
            sa.Column(
                "budget_type",
                sa.Enum(
                    "periodic", "pool", name="budget_type_enum", create_constraint=True
                ),
                nullable=False,
                server_default="periodic",
            ),
        )

    if not _column_exists(bind, "teams", "last_pool_purchase"):
        op.add_column(
            "teams",
            sa.Column("last_pool_purchase", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "teams", "last_pool_purchase"):
        op.drop_column("teams", "last_pool_purchase")
    if _column_exists(bind, "teams", "budget_type"):
        op.drop_column("teams", "budget_type")
