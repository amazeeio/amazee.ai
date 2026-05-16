"""add created_at index to stripe_processed_events

Revision ID: f0a1b2c3d4e5
Revises: 6e9b1c2d4f8a
Create Date: 2026-05-16 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f0a1b2c3d4e5"
down_revision = "6e9b1c2d4f8a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_stripe_processed_events_created_at"),
        "stripe_processed_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_stripe_processed_events_created_at"),
        table_name="stripe_processed_events",
    )
