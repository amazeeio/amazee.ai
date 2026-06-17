"""add model management tables

Revision ID: a12e3f4b5c6d
Revises: c4a9d8e1f2b3
Create Date: 2026-06-17 21:55:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "a12e3f4b5c6d"
down_revision = "c4a9d8e1f2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create models table
    op.create_table(
        "models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("real_eol", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_eol", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active_globally", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("litellm_params", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_models_id"), "models", ["id"], unique=False)
    op.create_index(
        "ix_models_model_id",
        "models",
        ["model_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    # Create model_regions table
    op.create_table(
        "model_regions",
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("region_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sync_status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("sync_error", sa.String(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["region_id"], ["regions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("model_id", "region_id"),
    )


def downgrade() -> None:
    op.drop_table("model_regions")
    op.drop_index("ix_models_model_id", table_name="models")
    op.drop_index(op.f("ix_models_id"), table_name="models")
    op.drop_table("models")
