"""add_budget_type_to_teams

Revision ID: a1b2c3d4e5f6
Revises: 1a2b3c4d5e6f
Create Date: 2026-03-13 00:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
