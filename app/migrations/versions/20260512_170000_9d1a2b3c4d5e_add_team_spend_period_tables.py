"""add team spend period tables

Revision ID: 9d1a2b3c4d5e
Revises: f1c2d3e4a5b6
Create Date: 2026-05-12 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d1a2b3c4d5e"
down_revision = "a474_periodic_payments"
branch_labels = None
depends_on = None


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

    if not _table_exists(bind, "team_spend_periods"):
        op.create_table(
            "team_spend_periods",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("region_id", sa.Integer(), nullable=False),
            sa.Column("budget_type", sa.String(), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("currency", sa.String(), nullable=True),
            sa.Column("total_spend", sa.Float(), nullable=False),
            sa.Column("total_budget", sa.Float(), nullable=True),
            sa.Column("total_prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("total_completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("stripe_event_id", sa.String(), nullable=True),
            sa.Column("stripe_invoice_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column("raw_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "team_id",
                "region_id",
                "budget_type",
                "period_start",
                "period_end",
                name="uq_team_spend_period_unique_window",
            ),
        )

    if not _index_exists(bind, "team_spend_periods", op.f("ix_team_spend_periods_id")):
        op.create_index(
            op.f("ix_team_spend_periods_id"),
            "team_spend_periods",
            ["id"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_team_id")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_team_id"),
            "team_spend_periods",
            ["team_id"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_region_id")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_region_id"),
            "team_spend_periods",
            ["region_id"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_budget_type")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_budget_type"),
            "team_spend_periods",
            ["budget_type"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_period_start")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_period_start"),
            "team_spend_periods",
            ["period_start"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_period_end")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_period_end"),
            "team_spend_periods",
            ["period_end"],
            unique=False,
        )
    if not _index_exists(
        bind, "team_spend_periods", op.f("ix_team_spend_periods_stripe_event_id")
    ):
        op.create_index(
            op.f("ix_team_spend_periods_stripe_event_id"),
            "team_spend_periods",
            ["stripe_event_id"],
            unique=False,
        )

    if not _table_exists(bind, "team_spend_period_keys"):
        op.create_table(
            "team_spend_period_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("team_spend_period_id", sa.Integer(), nullable=False),
            sa.Column("key_id", sa.Integer(), nullable=True),
            sa.Column("owner_id", sa.Integer(), nullable=True),
            sa.Column("key_name_snapshot", sa.String(), nullable=True),
            sa.Column("spend", sa.Float(), nullable=False),
            sa.Column("max_budget", sa.Float(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_tokens", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(["key_id"], ["ai_tokens.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["team_spend_period_id"], ["team_spend_periods.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "team_spend_period_id", "key_id", name="uq_team_spend_period_key"
            ),
        )

    if not _index_exists(
        bind, "team_spend_period_keys", op.f("ix_team_spend_period_keys_id")
    ):
        op.create_index(
            op.f("ix_team_spend_period_keys_id"),
            "team_spend_period_keys",
            ["id"],
            unique=False,
        )
    if not _index_exists(
        bind,
        "team_spend_period_keys",
        op.f("ix_team_spend_period_keys_team_spend_period_id"),
    ):
        op.create_index(
            op.f("ix_team_spend_period_keys_team_spend_period_id"),
            "team_spend_period_keys",
            ["team_spend_period_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_team_spend_period_keys_team_spend_period_id"),
        table_name="team_spend_period_keys",
    )
    op.drop_index(
        op.f("ix_team_spend_period_keys_id"), table_name="team_spend_period_keys"
    )
    op.drop_table("team_spend_period_keys")

    op.drop_index(
        op.f("ix_team_spend_periods_stripe_event_id"), table_name="team_spend_periods"
    )
    op.drop_index(
        op.f("ix_team_spend_periods_period_end"), table_name="team_spend_periods"
    )
    op.drop_index(
        op.f("ix_team_spend_periods_period_start"), table_name="team_spend_periods"
    )
    op.drop_index(
        op.f("ix_team_spend_periods_budget_type"), table_name="team_spend_periods"
    )
    op.drop_index(
        op.f("ix_team_spend_periods_region_id"), table_name="team_spend_periods"
    )
    op.drop_index(
        op.f("ix_team_spend_periods_team_id"), table_name="team_spend_periods"
    )
    op.drop_index(op.f("ix_team_spend_periods_id"), table_name="team_spend_periods")
    op.drop_table("team_spend_periods")
