"""add_pool_expiry_notification_ts

Revision ID: c1f2e3d4a5b6
Revises: b7e4d90f1c2a
Create Date: 2026-03-12 11:30:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1f2e3d4a5b6"
down_revision: Union[str, None] = "b7e4d90f1c2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "team_regions",
        sa.Column(
            "expiry_notification_sent_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("team_regions", "expiry_notification_sent_at")
