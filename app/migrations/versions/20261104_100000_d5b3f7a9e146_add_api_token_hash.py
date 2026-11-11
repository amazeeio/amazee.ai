"""add api token hash

Revision ID: d5b3f7a9e146
Revises: c4a9e6f2d817
Create Date: 2026-11-04 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d5b3f7a9e146"
down_revision = "c4a9e6f2d817"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_tokens", sa.Column("token_hash", sa.String(), nullable=True))
    # Plain SQL keeps the migration free of app imports; it matches hash_api_token.
    op.execute(
        "UPDATE api_tokens "
        "SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex') "
        "WHERE token IS NOT NULL AND token_hash IS NULL"
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_column("api_tokens", "token_hash")
