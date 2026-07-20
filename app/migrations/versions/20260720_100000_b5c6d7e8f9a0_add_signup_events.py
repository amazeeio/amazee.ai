"""add signup_events table for per-IP signup velocity limiting

Revision ID: b5c6d7e8f9a0
Revises: a3f2b1c0d9e8
Create Date: 2026-07-20 10:00:00.000000+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a3f2b1c0d9e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signup_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_signup_events_id"), "signup_events", ["id"])
    op.create_index(
        op.f("ix_signup_events_ip_address"), "signup_events", ["ip_address"]
    )
    op.create_index(
        op.f("ix_signup_events_created_at"), "signup_events", ["created_at"]
    )
    # Composite index for the hot query: recent events by IP.
    op.create_index(
        "ix_signup_events_ip_created",
        "signup_events",
        ["ip_address", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_signup_events_ip_created", table_name="signup_events")
    op.drop_index(op.f("ix_signup_events_created_at"), table_name="signup_events")
    op.drop_index(op.f("ix_signup_events_ip_address"), table_name="signup_events")
    op.drop_index(op.f("ix_signup_events_id"), table_name="signup_events")
    op.drop_table("signup_events")
