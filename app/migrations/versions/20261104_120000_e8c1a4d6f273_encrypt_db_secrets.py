"""encrypt db secrets

Revision ID: e8c1a4d6f273
Revises: f2d9b6c3a581
Create Date: 2026-11-04 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import InvalidToken

from app.core.crypto import fernet


# revision identifiers, used by Alembic.
revision = "e8c1a4d6f273"
down_revision = "f2d9b6c3a581"
branch_labels = None
depends_on = None

SECRET_COLUMNS = {
    "regions": ("litellm_api_key", "postgres_admin_password"),
    "ai_tokens": ("litellm_token", "database_password"),
}
BATCH_SIZE = 1000


def _convert_secrets(convert) -> None:
    # Autocommit keeps row locks short on large tables; the conversion is
    # idempotent, so a run that stops halfway is safe to repeat.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        for table, columns in SECRET_COLUMNS.items():
            last_id = 0
            while True:
                rows = connection.execute(
                    sa.text(
                        f"SELECT id, {', '.join(columns)} FROM {table} "
                        "WHERE id > :last ORDER BY id LIMIT :limit"
                    ),
                    {"last": last_id, "limit": BATCH_SIZE},
                ).fetchall()
                if not rows:
                    break
                for index, column in enumerate(columns, start=1):
                    updates = [
                        {"value": new_value, "id": row.id, "old": row[index]}
                        for row in rows
                        if row[index] and (new_value := convert(row[index])) is not None
                    ]
                    if updates:
                        # Skip a row that a running pod changed since the read; it wrote it encrypted.
                        connection.execute(
                            sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id AND {column} = :old"),
                            updates,
                        )
                last_id = rows[-1].id


def _decrypt(value: str) -> str | None:
    try:
        return fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def _encrypt(value: str) -> str | None:
    # A value that already decrypts is skipped, so a second run changes nothing.
    if _decrypt(value) is not None:
        return None
    return fernet().encrypt(value.encode()).decode()


def upgrade() -> None:
    _convert_secrets(_encrypt)


def downgrade() -> None:
    _convert_secrets(_decrypt)
