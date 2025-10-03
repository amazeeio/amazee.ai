""""Create enum value for SYSTEM limits"

Revision ID: 7704c20718a4
Revises: 39cf67d5a2ba
Create Date: 2025-10-02 13:54:38.230726+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7704c20718a4'
down_revision: Union[str, None] = '39cf67d5a2ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'SYSTEM' value to the ownertype enum
    op.execute("ALTER TYPE ownertype ADD VALUE 'SYSTEM'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values directly
    # This would require recreating the enum type and updating all references
    # For now, we'll leave the SYSTEM value in place
    pass