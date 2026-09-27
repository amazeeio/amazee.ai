"""add registry price scopes, cloud availability, lifecycle and proxy cloud regions

Revision ID: c9e3a7d5f041
Revises: b6d1f9a4c258
Create Date: 2026-09-27 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c9e3a7d5f041"
down_revision = "b6d1f9a4c258"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registry_model_prices",
        sa.Column(
            "model_id",
            sa.Integer(),
            sa.ForeignKey("registry_models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("scope_kind", sa.String(), primary_key=True),
        sa.Column("scope", sa.String(), primary_key=True),
        sa.Column("prices", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
    )
    op.create_table(
        "registry_cloud_availability",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("cloud_region", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("call_types", sa.JSON(), nullable=False),
        sa.Column("last_seen", sa.Date(), nullable=False),
    )
    op.create_table(
        "registry_model_lifecycle",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("launched_at", sa.Date(), nullable=True),
        sa.Column("legacy_at", sa.Date(), nullable=True),
        sa.Column("extended_access_until", sa.Date(), nullable=True),
        sa.Column("eol_date", sa.Date(), nullable=True),
        sa.Column("last_seen", sa.Date(), nullable=False),
    )
    op.add_column(
        "registry_model_support", sa.Column("region_available", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "registry_proxies",
        sa.Column("cloud_regions", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    # The AWS region serves both Bedrock providers.
    op.execute(
        "UPDATE registry_proxies SET cloud_regions = json_build_object("
        "'bedrock', aws_region_name, 'bedrock_mantle', aws_region_name) "
        "WHERE aws_region_name IS NOT NULL"
    )
    op.drop_column("registry_proxies", "aws_region_name")


def downgrade() -> None:
    op.add_column("registry_proxies", sa.Column("aws_region_name", sa.String(), nullable=True))
    op.execute("UPDATE registry_proxies SET aws_region_name = cloud_regions->>'bedrock'")
    op.drop_column("registry_proxies", "cloud_regions")
    op.drop_column("registry_model_support", "region_available")
    op.drop_table("registry_model_lifecycle")
    op.drop_table("registry_cloud_availability")
    op.drop_table("registry_model_prices")
