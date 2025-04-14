"""add role to users and customer management

Revision ID: 20250414_add_role_to_users
Revises: 835c19b87117
Create Date: 2025-04-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20250414_add_role_to_users'
down_revision = '835c19b87117'
branch_labels = None
depends_on = None


def upgrade():
    # Create customers table
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('billing_address', sa.String(255), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )

    # Add role column to users table
    op.add_column('users', sa.Column('role', sa.String(50), nullable=True))

    # Add customer_id column to users table
    op.add_column('users', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_users_customer_id',
        'users', 'customers',
        ['customer_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add updated_at column to users table
    op.add_column('users', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # Add customer_id column to ai_tokens table
    op.add_column('ai_tokens', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_ai_tokens_customer_id',
        'ai_tokens', 'customers',
        ['customer_id'], ['id'],
        ondelete='SET NULL'
    )

    #Add updated_at column to ai_tokens table
    op.add_column('ai_tokens', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # Set default role for existing users
    op.execute("UPDATE users SET role = 'user' WHERE role IS NULL")


def downgrade():
    # Remove customer_id column from ai_tokens table
    op.drop_constraint('fk_ai_tokens_customer_id', 'ai_tokens', type_='foreignkey')
    op.drop_column('ai_tokens', 'customer_id')

    # Remove customer_id column from users table
    op.drop_constraint('fk_users_customer_id', 'users', type_='foreignkey')
    op.drop_column('users', 'customer_id')

    # Remove updated_at column from users table
    op.drop_column('users', 'updated_at')

    # Remove role column from users table
    op.drop_column('users', 'role')

    # Remove updated_at column from ai_tokens table
    op.drop_column('ai_tokens', 'updated_at')

    # Drop customers table
    op.drop_table('customers')