"""add model aliases, per-region param overrides and regional areas

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-30 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which market a LiteLLM region serves (US, US+CA, EU, DE, CH, UK, AU, ...)
    # Enforced as a fixed enum at the API layer; plain string in the DB so new
    # areas don't need a type migration.
    op.add_column("regions", sa.Column("regional_area", sa.String(), nullable=True))

    # Per-region override merged over models.litellm_params at sync time —
    # typically just {"model": "bedrock/eu.anthropic..."} so the same catalog
    # model rolls out with a different backend ID per region.
    op.add_column(
        "model_regions",
        sa.Column("litellm_params_override", sa.JSON(), nullable=True),
    )

    # Alias models: catalog entries that point at another model per region and
    # inherit the target's effective litellm_params (pointer semantics).
    op.add_column(
        "models",
        sa.Column("is_alias", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_table(
        "model_alias_targets",
        sa.Column("alias_model_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("target_model_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["alias_model_id"], ["models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("alias_model_id", "region_id"),
    )


def downgrade() -> None:
    op.drop_table("model_alias_targets")
    op.drop_column("models", "is_alias")
    op.drop_column("model_regions", "litellm_params_override")
    op.drop_column("regions", "regional_area")
