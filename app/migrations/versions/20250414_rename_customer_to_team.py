"""rename customer to team

Revision ID: 20250414_rename_customer_to_team
Revises: 20250414_add_role_to_users
Create Date: 2025-04-14 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250414_rename_customer_to_team'
down_revision = '20250414_add_role_to_users'
branch_labels = None
depends_on = None


def upgrade():
    # Rename the customers table to teams
    op.rename_table('customers', 'teams')

    # Rename foreign key constraints
    op.drop_constraint('fk_users_customer_id', 'users', type_='foreignkey')
    op.drop_constraint('fk_ai_tokens_customer_id', 'ai_tokens', type_='foreignkey')

    # Rename columns
    op.alter_column('users', 'customer_id', new_column_name='team_id')
    op.alter_column('ai_tokens', 'customer_id', new_column_name='team_id')

    # Recreate foreign key constraints with new names
    op.create_foreign_key(
        'fk_users_team_id',
        'users', 'teams',
        ['team_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_ai_tokens_team_id',
        'ai_tokens', 'teams',
        ['team_id'], ['id'],
        ondelete='SET NULL'
    )


def downgrade():
    # Drop foreign key constraints
    op.drop_constraint('fk_users_team_id', 'users', type_='foreignkey')
    op.drop_constraint('fk_ai_tokens_team_id', 'ai_tokens', type_='foreignkey')

    # Rename columns back
    op.alter_column('users', 'team_id', new_column_name='customer_id')
    op.alter_column('ai_tokens', 'team_id', new_column_name='customer_id')

    # Recreate original foreign key constraints
    op.create_foreign_key(
        'fk_users_customer_id',
        'users', 'customers',
        ['customer_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_ai_tokens_customer_id',
        'ai_tokens', 'customers',
        ['customer_id'], ['id'],
        ondelete='SET NULL'
    )

    # Rename the teams table back to customers
    op.rename_table('teams', 'customers')