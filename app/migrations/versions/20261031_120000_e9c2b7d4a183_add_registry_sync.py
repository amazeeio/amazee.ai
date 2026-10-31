"""add registry sync

Revision ID: e9c2b7d4a183
Revises: c4a9e6f2d817
Create Date: 2026-10-31 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e9c2b7d4a183"
down_revision = "c4a9e6f2d817"
branch_labels = None
depends_on = None

_DEPLOYMENT_COLUMNS = ("synced_at", "changed_at", "sync_error")


def upgrade() -> None:
    op.create_table(
        "registry_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("values_encrypted", sa.Text(), nullable=True),
        sa.Column("credential_info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "registry_credential_regions",
        sa.Column(
            "credential_id",
            sa.Integer(),
            sa.ForeignKey("registry_credentials.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "registry_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("region_ids", sa.JSON(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "registry_sync_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("registry_sync_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("deployment_id", sa.String(), nullable=True),
        sa.Column(
            "model_region_id",
            sa.Integer(),
            sa.ForeignKey("registry_model_regions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("credential", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_registry_sync_steps_run_id", "registry_sync_steps", ["run_id"])
    op.add_column("registry_model_regions", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("registry_model_regions", sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("registry_model_regions", sa.Column("sync_error", sa.String(), nullable=True))


def downgrade() -> None:
    for column in _DEPLOYMENT_COLUMNS:
        op.drop_column("registry_model_regions", column)
    op.drop_index("ix_registry_sync_steps_run_id", table_name="registry_sync_steps")
    op.drop_table("registry_sync_steps")
    op.drop_table("registry_sync_runs")
    op.drop_table("registry_credential_regions")
    op.drop_table("registry_credentials")
