"""Add limit management

Revision ID: 73d59a43078d
Revises: add_team_metrics_table
Create Date: 2025-09-25 09:44:15.112849+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '73d59a43078d'
down_revision: Union[str, None] = 'add_team_metrics_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('limited_resources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('limit_type', sa.Enum('CONTROL_PLANE', 'DATA_PLANE', name='limittype'), nullable=False),
    sa.Column('resource', sa.Enum('KEY', 'USER', 'VECTOR_DB', 'GPT_INSTANCE', 'BUDGET', 'RPM', 'STORAGE', 'DOCUMENT', name='resourcetype'), nullable=False),
    sa.Column('unit', sa.Enum('COUNT', 'DOLLAR', 'GB', name='unittype'), nullable=False),
    sa.Column('max_value', sa.Float(), nullable=False),
    sa.Column('current_value', sa.Float(), nullable=True),
    sa.Column('owner_type', sa.Enum('TEAM', 'USER', name='ownertype'), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('limited_by', sa.Enum('PRODUCT', 'DEFAULT', 'MANUAL', name='limitsource'), nullable=False),
    sa.Column('set_by', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_type', 'owner_id', 'resource', name='uq_owner_resource')
    )
    op.create_index(op.f('ix_limited_resources_id'), 'limited_resources', ['id'], unique=False)


def downgrade() -> None:
    # op.drop_index(op.f('ix_limited_resources_id'), table_name='limited_resources')
    op.drop_table('limited_resources')

     # Drop the ENUMs that were created for this table
    op.execute('DROP TYPE IF EXISTS limittype')
    op.execute('DROP TYPE IF EXISTS resourcetype')
    op.execute('DROP TYPE IF EXISTS unittype')
    op.execute('DROP TYPE IF EXISTS ownertype')
    op.execute('DROP TYPE IF EXISTS limitsource')