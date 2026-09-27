"""add registry prices and rename the deprecated model status to removed

Revision ID: b6d1f9a4c258
Revises: a8c4e2f7b913
Create Date: 2026-09-27 11:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "b6d1f9a4c258"
down_revision = "a8c4e2f7b913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "registry_models",
        sa.Column("prices", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "registry_model_regions",
        sa.Column("prices", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # Keep the per-token prices already known; the daily job fills the rest.
    for table in ("registry_models", "registry_model_regions"):
        op.execute(
            f"UPDATE {table} SET prices = json_strip_nulls(json_build_object("
            "'input_cost_per_token', input_cost_per_token, "
            "'output_cost_per_token', output_cost_per_token))"
        )
    op.execute("UPDATE registry_models SET status = 'removed' WHERE status = 'deprecated'")


def downgrade() -> None:
    op.execute("UPDATE registry_models SET status = 'deprecated' WHERE status = 'removed'")
    op.drop_column("registry_model_regions", "prices")
    op.drop_column("registry_models", "prices")
