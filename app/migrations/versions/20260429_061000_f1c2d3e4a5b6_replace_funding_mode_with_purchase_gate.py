"""replace funding_mode with purchase gate flag

Revision ID: f1c2d3e4a5b6
Revises: d4f2b7c91a10
Create Date: 2026-04-29 06:10:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1c2d3e4a5b6"
down_revision: Union[str, None] = "d4f2b7c91a10"


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
    op.execute(
        """
        UPDATE teams
        SET require_purchase_for_requests = CASE
            WHEN funding_mode::text = 'invoice_usage' THEN false
            ELSE true
        END
        """
    )
    op.drop_column("teams", "funding_mode")
    op.execute("DROP TYPE IF EXISTS funding_mode_enum")


def downgrade() -> None:
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
        SET funding_mode = CASE
            WHEN require_purchase_for_requests IS TRUE THEN 'prepaid_pool'
            ELSE 'invoice_usage'
        END
        """
    )
    op.drop_column("teams", "require_purchase_for_requests")
