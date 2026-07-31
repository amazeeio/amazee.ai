"""Tenant isolation guarantees of vector-DB provisioning."""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from app.db.models import DBRegion
from app.db.postgres import PostgresManager


@pytest.fixture
def region():
    region = Mock(spec=DBRegion)
    region.postgres_host = "vectordb1.test"
    region.postgres_port = 5432
    region.postgres_admin_user = "admin"
    region.postgres_admin_password = "adminpw"
    return region


@pytest.mark.asyncio
async def test_create_database_revokes_public_connect(region):
    """A new tenant database must be connectable only by its own role.

    Without the REVOKE, PostgreSQL's default PUBLIC CONNECT grant lets any
    tenant role on the shared cluster open a session against any other tenant's
    database using only its own password.
    """
    conn = AsyncMock()
    with patch("asyncpg.connect", AsyncMock(return_value=conn)):
        result = await PostgresManager(region=region).create_database()

    statements = [call.args[0] for call in conn.execute.call_args_list]
    db_name = result["database_name"]
    db_user = result["database_username"]

    assert f"REVOKE CONNECT ON DATABASE {db_name} FROM PUBLIC" in statements
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in statements
    # The owning role keeps its own CONNECT (granted with ALL PRIVILEGES), and
    # gets it before PUBLIC loses it.
    grant = statements.index(f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}")
    revoke = statements.index(f"REVOKE CONNECT ON DATABASE {db_name} FROM PUBLIC")
    assert grant < revoke


@pytest.mark.asyncio
async def test_restrict_connect_to_owner_grants_before_revoking(region):
    """Backfill order matters: an interrupted run must not lock the tenant out."""
    conn = AsyncMock()
    with patch("asyncpg.connect", AsyncMock(return_value=conn)):
        await PostgresManager(region=region).restrict_connect_to_owner(
            "db_abc123", "user_abc123"
        )

    assert [call.args[0] for call in conn.execute.call_args_list] == [
        "GRANT CONNECT ON DATABASE db_abc123 TO user_abc123",
        "REVOKE CONNECT ON DATABASE db_abc123 FROM PUBLIC",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_name,database_username",
    [
        ("db_ok; DROP DATABASE db_other", "user_ok"),
        ("db_ok", 'user_ok"; DROP ROLE x'),
    ],
)
async def test_restrict_connect_to_owner_rejects_bad_identifiers(
    region, database_name, database_username
):
    with patch("asyncpg.connect", AsyncMock()) as mock_connect:
        with pytest.raises(ValueError):
            await PostgresManager(region=region).restrict_connect_to_owner(
                database_name, database_username
            )
    mock_connect.assert_not_called()
