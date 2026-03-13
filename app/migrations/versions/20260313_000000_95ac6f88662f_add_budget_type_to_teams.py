"""add_budget_type_to_teams

Revision ID: 95ac6f88662f
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-13 00:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic to track migration history
revision: str = "95ac6f88662f"  # noqa: F841
down_revision: Union[str, None] = "1a2b3c4d5e6f"  # noqa: F841


def upgrade() -> None:
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
    op.drop_column("teams", "budget_type")
