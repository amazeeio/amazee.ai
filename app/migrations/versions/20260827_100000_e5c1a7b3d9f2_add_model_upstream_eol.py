"""track upstream model EOL dates and whether they were announced

Revision ID: e5c1a7b3d9f2
Revises: f4c81a9d0b73
Create Date: 2026-08-27 10:00:00.000000+00:00

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "e5c1a7b3d9f2"
down_revision: Union[str, None] = "f4c81a9d0b73"


def upgrade() -> None:
    # Separate from real_eol/override_eol on purpose: the model catalog apply
    # rewrites those from its CSV on every run, so a second writer would fight
    # it. These three columns are written only by the EOL scan.
    op.add_column(
        "models", sa.Column("upstream_eol", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "models",
        sa.Column(
            "upstream_eol_first_seen_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "models",
        sa.Column("eol_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("models", "eol_notified_at")
    op.drop_column("models", "upstream_eol_first_seen_at")
    op.drop_column("models", "upstream_eol")
