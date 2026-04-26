"""add user_admin_regions and backfill team_regions

Revision ID: f4d2c6a9b1e7
Revises: c8f9a2d1b4e0
Create Date: 2026-04-26 18:30:00.000000+00:00

"""

from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f4d2c6a9b1e7"
down_revision: Union[str, None] = "c8f9a2d1b4e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_admin_regions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "region_id"),
    )

    bind = op.get_bind()
    teams = sa.table(
        "teams",
        sa.column("id", sa.Integer),
        sa.column("hide_public_regions", sa.Boolean),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    regions = sa.table(
        "regions",
        sa.column("id", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("is_dedicated", sa.Boolean),
    )
    team_regions = sa.table(
        "team_regions",
        sa.column("team_id", sa.Integer),
        sa.column("region_id", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    team_ids = [
        row.id
        for row in bind.execute(
            sa.select(teams.c.id).where(
                teams.c.deleted_at.is_(None),
                sa.or_(
                    teams.c.hide_public_regions.is_(False),
                    teams.c.hide_public_regions.is_(None),
                ),
            )
        )
    ]
    region_ids = [
        row.id
        for row in bind.execute(
            sa.select(regions.c.id).where(
                regions.c.is_active.is_(True), regions.c.is_dedicated.is_(False)
            )
        )
    ]

    if not team_ids or not region_ids:
        return

    now = datetime.now(UTC)
    values = [
        {"team_id": team_id, "region_id": region_id, "created_at": now}
        for team_id in team_ids
        for region_id in region_ids
    ]
    if bind.dialect.name == "postgresql":
        stmt = (
            postgresql.insert(team_regions)
            .values(values)
            .on_conflict_do_nothing(index_elements=["team_id", "region_id"])
        )
        bind.execute(stmt)
    else:
        for value in values:
            bind.execute(sa.insert(team_regions).prefix_with("OR IGNORE").values(value))


def downgrade() -> None:
    op.drop_table("user_admin_regions")
