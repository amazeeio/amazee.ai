"""private-ai-keys--create

Revision ID: 835c19b87117
Revises: 20250219_rename_fields
Create Date: 2025-02-19 18:41:42.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '835c19b87117'
down_revision: Union[str, None] = '20250219_rename_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add created_at column with default value of current timestamp
    op.add_column('ai_tokens', sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')))


def downgrade() -> None:
    # Remove created_at column
    op.drop_column('ai_tokens', 'created_at')