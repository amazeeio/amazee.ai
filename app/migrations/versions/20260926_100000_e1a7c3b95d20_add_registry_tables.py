"""add model registry tables

Revision ID: e1a7c3b95d20
Revises: a3f9c6e1d7b2
Create Date: 2026-09-26 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e1a7c3b95d20"
down_revision = "a3f9c6e1d7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "registry_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "provider_id",
            sa.Integer(),
            sa.ForeignKey("registry_providers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("max_input_tokens", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_cost_per_token", sa.Numeric(), nullable=True),
        sa.Column("output_cost_per_token", sa.Numeric(), nullable=True),
        sa.Column("supports", sa.JSON(), nullable=False),
        sa.Column("eol_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("first_seen", sa.Date(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("provider_id", "model_id"),
    )
    op.create_table(
        "registry_access_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "registry_proxies",
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("litellm_version", sa.String(), nullable=True),
        sa.Column("aws_region_name", sa.String(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "registry_model_regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("registry_models.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("litellm_model", sa.String(), nullable=False),
        sa.Column("litellm_deployment_id", sa.String(), nullable=True),
        sa.Column("input_cost_per_token", sa.Numeric(), nullable=True),
        sa.Column("output_cost_per_token", sa.Numeric(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("region_id", "litellm_deployment_id"),
    )
    op.create_table(
        "registry_model_region_groups",
        sa.Column(
            "model_region_id",
            sa.Integer(),
            sa.ForeignKey("registry_model_regions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "access_group_id",
            sa.Integer(),
            sa.ForeignKey("registry_access_groups.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_table(
        "registry_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("step", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("registry_runs")
    op.drop_table("registry_model_region_groups")
    op.drop_table("registry_model_regions")
    op.drop_table("registry_proxies")
    op.drop_table("registry_access_groups")
    op.drop_table("registry_models")
    op.drop_table("registry_providers")
