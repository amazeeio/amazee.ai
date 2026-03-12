"""set_default_budget_mode_pool

Revision ID: 9f8e7d6c5b4a
Revises: c1f2e3d4a5b6
Create Date: 2026-03-12 18:05:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f8e7d6c5b4a"
down_revision: Union[str, None] = "c1f2e3d4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows are intentionally untouched. Only future inserts default to pool.
    op.alter_column(
        "teams", "budget_mode", existing_type=sa.String(), server_default="pool"
    )


def downgrade() -> None:
    op.alter_column(
        "teams", "budget_mode", existing_type=sa.String(), server_default="periodic"
    )
