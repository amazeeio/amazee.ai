"""drop stripe_processed_events

Revision ID: f4c81a9d0b73
Revises: e8f9a0b1c2d3
Create Date: 2026-08-13 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "f4c81a9d0b73"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The table only served idempotency for the legacy Stripe webhook, which
    # no longer exists. MOAD /cycle uses periodic_payments.stripe_payment_id.
    op.drop_index(
        op.f("ix_stripe_processed_events_created_at"),
        table_name="stripe_processed_events",
    )
    op.drop_index(
        op.f("ix_stripe_processed_events_stripe_event_id"),
        table_name="stripe_processed_events",
    )
    op.drop_index(
        op.f("ix_stripe_processed_events_id"), table_name="stripe_processed_events"
    )
    op.drop_table("stripe_processed_events")


def downgrade() -> None:
    op.create_table(
        "stripe_processed_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stripe_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )
    op.create_index(
        op.f("ix_stripe_processed_events_id"),
        "stripe_processed_events",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stripe_processed_events_stripe_event_id"),
        "stripe_processed_events",
        ["stripe_event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_stripe_processed_events_created_at"),
        "stripe_processed_events",
        ["created_at"],
    )
