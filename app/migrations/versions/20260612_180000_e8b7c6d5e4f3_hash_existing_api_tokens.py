"""hash existing api tokens

Revision ID: e8b7c6d5e4f3
Revises: d9a1b2c3e4f5
Create Date: 2026-06-12 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
import hashlib

revision = "e8b7c6d5e4f3"
down_revision = "c4a9d8e1f2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Get database connection
    connection = op.get_bind()
    
    # Check if table exists
    inspector = sa.inspect(connection)
    if not inspector.has_table("api_tokens"):
        return

    # Define a minimal table structure for the migration
    metadata = sa.MetaData()
    api_tokens = sa.Table(
        "api_tokens",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token", sa.String, nullable=False),
    )
    
    # Select all existing API tokens
    results = connection.execute(sa.select(api_tokens.c.id, api_tokens.c.token)).fetchall()
    
    for row in results:
        token_id = row[0]
        token_val = row[1]
        
        # Plaintext tokens are usually 43 characters (from token_urlsafe(32)), 
        # while SHA-256 hex digests are exactly 64 characters long.
        if token_val and len(token_val) != 64:
            # Hash the plaintext token
            hashed = hashlib.sha256(token_val.encode("utf-8")).hexdigest()
            # Update the record in the database
            connection.execute(
                api_tokens.update()
                .where(api_tokens.c.id == token_id)
                .values(token=hashed)
            )


def downgrade() -> None:
    # Hashing is a one-way operation and cannot be reversed to restore plaintext values.
    # No-op in downgrade is appropriate.
    pass
