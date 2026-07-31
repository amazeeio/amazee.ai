"""add api token scope

Revision ID: f1a2b3c4d5e6
Revises: daf5bf0b03c2
Create Date: 2026-07-31 13:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "daf5bf0b03c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    columns = [c["name"] for c in sa.inspect(conn).get_columns("api_tokens")]

    # Guarded because some environments are bootstrapped from the models
    # (`create_all` + `stamp head` in scripts/initialise_resources.py).
    if "scope" not in columns:
        op.add_column(
            "api_tokens",
            sa.Column("scope", sa.String(), nullable=False, server_default="read"),
        )

    # Every existing token becomes read-only except those owned by a system
    # admin. Admin-owned tokens are the machine integrations that write today
    # (e.g. PolyDock creating keys), so they keep working across this deploy.
    #
    # Tokens owned by non-admin users — including moad's per-user
    # `drupal_management_token` rows, which POST /private-ai-keys — become
    # read-only and will get 403 on writes until they are re-created with
    # `scope: "write"`.
    conn.execute(
        sa.text(
            "UPDATE api_tokens SET scope = 'write' "
            "WHERE user_id IN (SELECT id FROM users WHERE is_admin IS TRUE)"
        )
    )


def downgrade() -> None:
    columns = [c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_tokens")]

    if "scope" in columns:
        op.drop_column("api_tokens", "scope")
