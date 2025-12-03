"""merge heads

Revision ID: 20250219_merge_heads
Revises: b939aa41a265, 20250219_add_name
Create Date: 2025-02-19 00:00:00.000000+00:00

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '20250219_merge_heads'
down_revision: tuple[str, str] = ('b939aa41a265', '20250219_add_name')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass