"""add created_at to users

Revision ID: 20250218_140000
Revises: 07f783a5311d
Create Date: 2024-02-18 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250218_140000'
down_revision: Union[str, None] = '07f783a5311d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add created_at column to users table
    op.add_column('users', sa.Column('created_at', sa.DateTime(), nullable=True))
    # Set default value for existing rows
    op.execute("UPDATE users SET created_at = CURRENT_TIMESTAMP")
    # Make the column not nullable after setting default values
    op.alter_column('users', 'created_at', nullable=False)


def downgrade() -> None:
    # Remove created_at column from users table
    op.drop_column('users', 'created_at')