"""delete stripe webhook secret

Revision ID: f2d9b6c3a581
Revises: d5b3f7a9e146
Create Date: 2026-11-04 11:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f2d9b6c3a581"
down_revision = "d5b3f7a9e146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM system_secrets WHERE key = 'stripe_webhook_secret'")


def downgrade() -> None:
    # The deleted secret value is not restored.
    pass
