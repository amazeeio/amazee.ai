"""add region_id to teams

Revision ID: a1b2c3d4e5f6
Revises: 2f7c9d1e4aab
Create Date: 2026-05-27 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "2f7c9d1e4aab"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add nullable region_id column
    op.add_column(
        "teams",
        sa.Column("region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=True),
    )

    # 2. Backfill from team_regions: prefer non-dedicated regions, then any region
    op.execute(
        """
        UPDATE teams
        SET region_id = (
            SELECT tr.region_id
            FROM team_regions tr
            JOIN regions r ON r.id = tr.region_id
            WHERE tr.team_id = teams.id
            ORDER BY r.is_dedicated ASC, tr.created_at ASC
            LIMIT 1
        )
        WHERE region_id IS NULL
        """
    )

    # 3. For teams that still have no region (zero team_regions rows),
    #    assign the first active non-dedicated region as a safe fallback.
    op.execute(
        """
        UPDATE teams
        SET region_id = (
            SELECT id FROM regions
            WHERE is_active = true AND is_dedicated = false
            ORDER BY id ASC
            LIMIT 1
        )
        WHERE region_id IS NULL
        """
    )

    # 4. Make NOT NULL — all rows must be populated after the backfill above.
    op.alter_column("teams", "region_id", nullable=False)


def downgrade():
    op.drop_column("teams", "region_id")
