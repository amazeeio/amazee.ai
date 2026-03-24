"""add_budget_type_to_teams

Revision ID: 95ac6f88662f
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-13 00:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic to track migration history
revision: str = "95ac6f88662f"
down_revision: Union[str, None] = "1a2b3c4d5e6f"


def upgrade() -> None:
    # Ensure the enum type exists
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


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = insp.get_columns("teams")
    if any(col["name"] == "budget_type" for col in cols):
        op.drop_column("teams", "budget_type")
    
    # Check if the enum type exists before dropping it
    op.execute("DROP TYPE IF EXISTS budget_type_enum")
