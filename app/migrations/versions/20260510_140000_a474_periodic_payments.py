"""Add periodic_payments table

Revision ID: a474_periodic_payments
Revises: f1c2d3e4a5b6
Create Date: 2026-05-10 14:00:00.000000+00:00

"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "a474_periodic_payments"
down_revision: Union[str, None] = "f1c2d3e4a5b6"


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(bind)
    try:
        indexes = insp.get_indexes(table_name)
    except sa.exc.NoSuchTableError:
        return False
    return any(idx.get("name") == index_name for idx in indexes)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "periodic_payments"):
        op.create_table(
            "periodic_payments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("stripe_payment_id", sa.String(), nullable=False),
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(), nullable=False),
            sa.Column("payment_type", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column(
                "sync_status", sa.String(), nullable=False, server_default="pending"
            ),
            sa.Column("error_log", sa.Text(), nullable=True),
            sa.Column("payment_date", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists(bind, "periodic_payments", op.f("ix_periodic_payments_id")):
        op.create_index(
            op.f("ix_periodic_payments_id"), "periodic_payments", ["id"], unique=False
        )
    if not _index_exists(
        bind, "periodic_payments", op.f("ix_periodic_payments_stripe_payment_id")
    ):
        op.create_index(
            op.f("ix_periodic_payments_stripe_payment_id"),
            "periodic_payments",
            ["stripe_payment_id"],
            unique=True,
        )
    if not _index_exists(
        bind, "periodic_payments", op.f("ix_periodic_payments_team_id")
    ):
        op.create_index(
            op.f("ix_periodic_payments_team_id"),
            "periodic_payments",
            ["team_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_periodic_payments_team_id"), table_name="periodic_payments")
    op.drop_index(
        op.f("ix_periodic_payments_stripe_payment_id"), table_name="periodic_payments"
    )
    op.drop_index(op.f("ix_periodic_payments_id"), table_name="periodic_payments")
    op.drop_table("periodic_payments")
