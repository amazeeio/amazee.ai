"""add region_id to teams

Revision ID: 6c201dbaea6e
Revises: a3f2b1c0d9e8
Create Date: 2026-05-27 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6c201dbaea6e"
down_revision = "a3f2b1c0d9e8"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add nullable region_id column
    op.add_column(
        "teams",
        sa.Column(
            "region_id", sa.Integer(), sa.ForeignKey("regions.id"), nullable=True
        ),
    )

    # 2. Backfill region_id. Prefer the active region where the team actually
    #    has the most keys (its real operational region); only fall back to the
    #    earliest non-dedicated team_regions association when a team has no keys.
    #
    #    Historically every team was seeded with ALL public regions at creation,
    #    so the old "earliest association" heuristic collapsed almost every team
    #    onto whichever public region happened to be inserted first — not where
    #    its keys live. Keying off actual key activity keeps region_id truthful,
    #    which matters because team.region_id now drives the team's visible
    #    region and LiteLLM member-sync target.
    op.execute(
        """
        UPDATE teams
        SET region_id = COALESCE(
            (
                SELECT k.region_id
                FROM ai_tokens k
                JOIN regions kr ON kr.id = k.region_id
                WHERE k.team_id = teams.id
                  AND k.region_id IS NOT NULL
                  AND kr.is_active = true
                GROUP BY k.region_id
                ORDER BY count(*) DESC, k.region_id ASC
                LIMIT 1
            ),
            (
                SELECT tr.region_id
                FROM team_regions tr
                JOIN regions r ON r.id = tr.region_id
                WHERE tr.team_id = teams.id
                ORDER BY r.is_dedicated ASC, tr.created_at ASC
                LIMIT 1
            )
        )
        WHERE region_id IS NULL
        """
    )

    # 3. For teams that still have no region (zero team_regions rows),
    #    assign the first active non-dedicated region as a safe fallback
    #    and insert a matching team_regions association row.
    bind = op.get_bind()
    fallback_row = bind.execute(
        sa.text(
            "SELECT id FROM regions"
            " WHERE is_active = true AND is_dedicated = false"
            " ORDER BY id ASC LIMIT 1"
        )
    ).fetchone()
    if fallback_row is None:
        raise RuntimeError(
            "No active non-dedicated region found; cannot backfill teams.region_id. "
            "Create at least one active non-dedicated region before running this migration."
        )
    fallback_id = fallback_row[0]
    # Insert team_regions rows for teams that have no association yet.
    op.execute(
        sa.text(
            """
            INSERT INTO team_regions (team_id, region_id, created_at)
            SELECT id, :fallback_id, NOW()
            FROM teams
            WHERE region_id IS NULL
            ON CONFLICT DO NOTHING
            """
        ).bindparams(fallback_id=fallback_id)
    )
    op.execute(
        sa.text(
            "UPDATE teams SET region_id = :fallback_id WHERE region_id IS NULL"
        ).bindparams(fallback_id=fallback_id)
    )

    # 4. Create index for region_id lookups
    op.create_index("ix_teams_region_id", "teams", ["region_id"])

    # 5. Keep nullable for backward compatibility with existing code paths that
    #    create teams before their default region is assigned.


def downgrade():
    op.drop_index("ix_teams_region_id", table_name="teams")
    op.drop_column("teams", "region_id")
