"""drop plain api token

Revision ID: a3e7c5f1d924
Revises: e8c1a4d6f273
Create Date: 2026-11-04 13:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "a3e7c5f1d924"
down_revision = "e8c1a4d6f273"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A rolled-back release can write tokens without a hash; hash them before the plain value goes.
    op.execute(
        "UPDATE api_tokens "
        "SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex') "
        "WHERE token IS NOT NULL AND token_hash IS NULL"
    )
    op.drop_column("api_tokens", "token")


def downgrade() -> None:
    # The plain tokens cannot come back; the column returns empty.
    op.add_column("api_tokens", sa.Column("token", sa.String(), nullable=True))
    op.create_index("ix_api_tokens_token", "api_tokens", ["token"], unique=True)
