"""delete limited_resources rows whose team or user no longer exists

Revision ID: b2c3d4e5f6a7
Revises: f9a0b1c2d3e4
Create Date: 2026-08-19 10:00:00.000000+00:00

"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic (read via module reflection).
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "f9a0b1c2d3e4"


def upgrade() -> None:
    # owner_id is a plain integer column with no foreign key, so rows survived
    # the deletion of their team or user. A later owner that gets the same id
    # inherits a counter that is already used up. System rows own no entity and
    # must stay.
    op.execute(
        """
        DELETE FROM limited_resources
        WHERE owner_type = 'TEAM'
          AND NOT EXISTS (
              SELECT 1 FROM teams WHERE teams.id = limited_resources.owner_id
          )
        """
    )
    op.execute(
        """
        DELETE FROM limited_resources
        WHERE owner_type = 'USER'
          AND NOT EXISTS (
              SELECT 1 FROM users WHERE users.id = limited_resources.owner_id
          )
        """
    )


def downgrade() -> None:
    # The deleted rows describe owners that no longer exist, so there is
    # nothing to restore them from.
    pass
