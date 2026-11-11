"""hash unhashed api tokens

Revision ID: 64640a2c1b9d
Revises: f2d9b6c3a581
Create Date: 2026-11-04 11:30:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "64640a2c1b9d"
down_revision = "f2d9b6c3a581"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A database created from the models and stamped at head never had the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_tokens")}
    if "token" not in columns:
        return
    # The previous release can write tokens without a hash, and the earlier backfill does not run again.
    op.execute(
        "UPDATE api_tokens "
        "SET token_hash = encode(sha256(convert_to(token, 'UTF8')), 'hex') "
        "WHERE token IS NOT NULL AND token_hash IS NULL"
    )


def downgrade() -> None:
    # The hashes stay valid for the plain tokens, so there is nothing to undo.
    pass
