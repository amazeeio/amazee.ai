"""add referer and origin to audit_logs

Revision ID: a3f2b1c0d9e8
Revises: c4a9d8e1f2b3
Create Date: 2026-07-08 09:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3f2b1c0d9e8"
down_revision: Union[str, None] = "c4a9d8e1f2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("referer", sa.String(), nullable=True))
    op.add_column("audit_logs", sa.Column("origin", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "origin")
    op.drop_column("audit_logs", "referer")
