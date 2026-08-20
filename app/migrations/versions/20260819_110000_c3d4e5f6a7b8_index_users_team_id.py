"""index users.team_id for the member-limit recount

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-19 11:00:00.000000+00:00

"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"


def upgrade() -> None:
    # The member-limit check counts a team's users on every member addition;
    # without an index that count scans the whole users table.
    op.create_index("ix_users_team_id", "users", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_users_team_id", table_name="users")
