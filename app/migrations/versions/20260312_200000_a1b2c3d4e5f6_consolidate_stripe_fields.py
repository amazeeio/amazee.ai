"""consolidate stripe fields

Revision ID: a1b2c3d4e5f6
Revises: 9f8e7d6c5b4a
Create Date: 2026-03-12 20:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "9f8e7d6c5b4a"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "budget_purchases_stripe_session_id_key", "budget_purchases", type_="unique"
    )
    op.drop_index(
        "ix_budget_purchases_stripe_payment_intent_id", table_name="budget_purchases"
    )
    op.alter_column(
        "budget_purchases",
        "stripe_payment_intent_id",
        new_column_name="stripe_transaction_id",
        nullable=False,
    )
    op.create_unique_constraint(
        "budget_purchases_stripe_transaction_id_key",
        "budget_purchases",
        ["stripe_transaction_id"],
    )
    op.drop_column("budget_purchases", "stripe_session_id")


def downgrade():
    op.add_column(
        "budget_purchases",
        sa.Column("stripe_session_id", sa.String(), nullable=True),
    )
    op.drop_constraint(
        "budget_purchases_stripe_transaction_id_key", "budget_purchases", type_="unique"
    )
    op.alter_column(
        "budget_purchases",
        "stripe_transaction_id",
        new_column_name="stripe_payment_intent_id",
        nullable=True,
    )
    op.create_unique_constraint(
        "budget_purchases_stripe_session_id_key",
        "budget_purchases",
        ["stripe_session_id"],
    )
    op.create_index(
        "ix_budget_purchases_stripe_payment_intent_id",
        "budget_purchases",
        ["stripe_payment_intent_id"],
    )
    op.execute(
        "UPDATE budget_purchases SET stripe_session_id = stripe_payment_intent_id"
    )
