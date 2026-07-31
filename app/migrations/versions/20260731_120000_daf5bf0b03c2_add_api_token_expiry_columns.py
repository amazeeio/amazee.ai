"""add api token expiry columns

Revision ID: daf5bf0b03c2
Revises: e5b8c7d2a941
Create Date: 2026-07-31 12:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "daf5bf0b03c2"
down_revision: Union[str, None] = "e5b8c7d2a941"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guarded because some environments are bootstrapped from the models
    # (`create_all` + `stamp head` in scripts/initialise_resources.py) and
    # already have these columns.
    columns = [c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_tokens")]

    if "expires_at" not in columns:
        op.add_column(
            "api_tokens",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "expiry_option" not in columns:
        # Existing tokens have no expiry, which is exactly "forever".
        op.add_column(
            "api_tokens",
            sa.Column(
                "expiry_option", sa.String(), nullable=False, server_default="forever"
            ),
        )


def downgrade() -> None:
    columns = [c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_tokens")]

    if "expiry_option" in columns:
        op.drop_column("api_tokens", "expiry_option")
    if "expires_at" in columns:
        op.drop_column("api_tokens", "expires_at")
