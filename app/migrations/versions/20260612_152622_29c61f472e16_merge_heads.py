"""merge heads

Revision ID: 29c61f472e16
Revises: c4a9d8e1f2b3, d9a1b2c3e4f5
Create Date: 2026-06-12 15:26:22.818531+00:00

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '29c61f472e16'
down_revision: Union[str, Sequence[str], None] = ('c4a9d8e1f2b3', 'd9a1b2c3e4f5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass