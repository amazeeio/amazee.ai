"""add funding_mode to teams

Revision ID: d4f2b7c91a10
Revises: c8f9a2d1b4e0
Create Date: 2026-04-28 17:30:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f2b7c91a10"
down_revision: Union[str, None] = "c8f9a2d1b4e0"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'funding_mode_enum') THEN
                CREATE TYPE funding_mode_enum AS ENUM ('invoice_usage', 'prepaid_pool');
            END IF;
        END $$;
        """
    )
    op.add_column(
        "teams",
        sa.Column(
            "funding_mode",
            sa.Enum(
                "invoice_usage",
                "prepaid_pool",
                name="funding_mode_enum",
                create_constraint=True,
            ),
            nullable=False,
            server_default="invoice_usage",
        ),
    )
    op.execute(
        """
        UPDATE teams
        SET funding_mode = 'prepaid_pool'
        WHERE budget_type::text = 'pool'
        """
    )


def downgrade() -> None:
    op.drop_column("teams", "funding_mode")
    op.execute("DROP TYPE IF EXISTS funding_mode_enum")
