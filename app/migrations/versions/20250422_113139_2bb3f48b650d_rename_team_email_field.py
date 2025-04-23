"""Rename team email field

Revision ID: 2bb3f48b650d
Revises: 20250414_rename_customer_to_team
Create Date: 2025-04-22 11:31:39.684629+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2bb3f48b650d'
down_revision: Union[str, None] = '20250414_rename_customer_to_team'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('teams', 'email', new_column_name='admin_email')

def downgrade() -> None:
    op.alter_column('teams', 'admin_email', new_column_name='email')