"""add disposable_domains table (trial-account abuse protection) and seed baseline

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-20 11:00:00.000000+00:00

The daily refresh cron repopulates this table from the upstream disposable list.
We seed the committed baseline here so disposable domains are blocked immediately
after deploy, before the first cron run.
"""

from pathlib import Path
from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b5c6d7e8f9a0"

# app/migrations/versions/<this> -> parents[2] == app/
_BASELINE_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "disposable_domains_extra.txt"
)


def _baseline_domains() -> list[str]:
    try:
        lines = _BASELINE_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = set()
    for raw in lines:
        line = raw.strip().lower()
        if not line or line.startswith("#"):
            continue
        if "@" in line:
            line = line.rsplit("@", 1)[-1]
        line = line.strip(".")
        if line:
            out.add(line)
    return sorted(out)


def upgrade() -> None:
    op.create_table(
        "disposable_domains",
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("domain"),
    )
    domains = _baseline_domains()
    if domains:
        op.bulk_insert(
            sa.table(
                "disposable_domains",
                sa.column("domain", sa.String),
                sa.column("source", sa.String),
            ),
            [{"domain": d, "source": "baseline"} for d in domains],
        )


def downgrade() -> None:
    op.drop_table("disposable_domains")
