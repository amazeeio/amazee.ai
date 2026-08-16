"""add model access groups

Revision ID: d7e8f9a0b1c2
Revises: a12e3f4b5c6d
Create Date: 2026-07-29 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "a12e3f4b5c6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_access_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_access_groups_id"), "model_access_groups", ["id"], unique=False)
    op.create_index(op.f("ix_model_access_groups_slug"), "model_access_groups", ["slug"], unique=True)

    op.create_table(
        "model_access_group_models",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["model_access_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "model_id"),
    )

    op.create_table(
        "model_access_group_regions",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["model_access_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "region_id"),
    )

    op.create_table(
        "team_model_access_groups",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["model_access_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "group_id"),
    )

    op.create_table(
        "team_group_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_team_ids", sa.JSON(), nullable=True),
        sa.Column("error_sample", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_team_group_sync_runs_id"), "team_group_sync_runs", ["id"], unique=False)
    op.create_index(
        op.f("ix_team_group_sync_runs_region_id"), "team_group_sync_runs", ["region_id"], unique=False
    )

    # NULL = enforcement off (legacy all-models). RESTRICT prevents deleting a
    # group that is any region's default.
    op.add_column(
        "regions",
        sa.Column("default_access_group_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_regions_default_access_group_id",
        "regions",
        "model_access_groups",
        ["default_access_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_regions_default_access_group_id", "regions", type_="foreignkey")
    op.drop_column("regions", "default_access_group_id")
    op.drop_index(op.f("ix_team_group_sync_runs_region_id"), table_name="team_group_sync_runs")
    op.drop_index(op.f("ix_team_group_sync_runs_id"), table_name="team_group_sync_runs")
    op.drop_table("team_group_sync_runs")
    op.drop_table("team_model_access_groups")
    op.drop_table("model_access_group_regions")
    op.drop_table("model_access_group_models")
    op.drop_index(op.f("ix_model_access_groups_slug"), table_name="model_access_groups")
    op.drop_index(op.f("ix_model_access_groups_id"), table_name="model_access_groups")
    op.drop_table("model_access_groups")
