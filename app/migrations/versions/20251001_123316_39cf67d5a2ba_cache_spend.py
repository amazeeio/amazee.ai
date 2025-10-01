"""cache_spend

Revision ID: 39cf67d5a2ba
Revises: 73d59a43078d
Create Date: 2025-10-01 12:33:16.677042+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '39cf67d5a2ba'
down_revision: Union[str, None] = '73d59a43078d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_tokens', sa.Column('cached_spend', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_tokens', 'cached_spend')