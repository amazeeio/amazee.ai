"""rename private ai key fields

Revision ID: 20250219_rename_fields
Revises: 20250219_merge_heads
Create Date: 2025-02-19 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20250219_rename_fields'
down_revision: Union[str, None] = '20250219_merge_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename columns in ai_tokens table
    op.alter_column('ai_tokens', 'host', new_column_name='database_host')
    op.alter_column('ai_tokens', 'username', new_column_name='database_username')
    op.alter_column('ai_tokens', 'password', new_column_name='database_password')


def downgrade() -> None:
    # Revert column renames in ai_tokens table
    op.alter_column('ai_tokens', 'database_host', new_column_name='host')
    op.alter_column('ai_tokens', 'database_username', new_column_name='username')
    op.alter_column('ai_tokens', 'database_password', new_column_name='password')