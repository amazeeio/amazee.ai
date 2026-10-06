"""Tenant isolation guarantees of vector-DB provisioning."""

import os
import sys

import pytest
from unittest.mock import AsyncMock, Mock, patch

from app.db.models import DBRegion
from app.db.postgres import (
    PostgresManager,
    postgres_manager_for_key,
    regions_by_postgres_host,
)


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


def test_backfill_includes_regions_with_no_tracked_keys():
    """A region whose key rows were deleted without dropping their databases
    must still be visited, or its orphans are never reported."""
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from scripts.lock_vector_db_connect import get_vector_dbs_grouped_by_region

    tracked, orphan_only = Mock(spec=DBRegion), Mock(spec=DBRegion)
    tracked.id, orphan_only.id = 1, 2
    session = Mock()
    query = session.query.return_value
    query.filter.return_value.all.return_value = [tracked, orphan_only]
    query.filter.return_value.filter.return_value.filter.return_value.all.return_value = [
        (1, "db_abc123", "user_abc123")
    ]

    work = dict(get_vector_dbs_grouped_by_region(session, None))
    assert work[tracked] == [("db_abc123", "user_abc123")]
    assert work[orphan_only] == []


@pytest.mark.asyncio
async def test_host_override_keeps_region_credentials(region):
    connect = AsyncMock(return_value=AsyncMock())
    with patch("asyncpg.connect", connect):
        await PostgresManager(region=region, host="other.test").delete_database(
            "db_x", "user_x"
        )

    connect.assert_awaited_once_with(
        host="other.test", port=5432, user="admin", password="adminpw"
    )


def _region(host, is_active=True, port=5432):
    region = Mock(spec=DBRegion)
    region.postgres_host = host
    region.is_active = is_active
    region.postgres_port = port
    return region


def test_postgres_manager_for_key_picks_host_owner():
    own = _region("own.test")
    other = _region("other.test")
    regions_by_host = {"own.test": own, "other.test": other}

    def user_and_host(database_host, key_region=own):
        key = Mock(database_host=database_host)
        manager = postgres_manager_for_key(key, key_region, regions_by_host)
        return manager.admin_user, manager.host

    assert user_and_host(None) == (own.postgres_admin_user, "own.test")
    assert user_and_host("other.test") == (other.postgres_admin_user, "other.test")
    assert user_and_host("unknown.test") == (own.postgres_admin_user, "unknown.test")
    # An inactive key region gives way to the active region on its host.
    inactive_own = _region("own.test", is_active=False)
    assert user_and_host(None, inactive_own) == (own.postgres_admin_user, "own.test")
    # An active region on the same host and port still takes over.
    same_host_owner = _region("shared.test")
    inactive_same_port = _region("shared.test", is_active=False)
    manager = postgres_manager_for_key(
        Mock(database_host=None),
        inactive_same_port,
        {"shared.test": same_host_owner},
    )
    assert manager.admin_user == same_host_owner.postgres_admin_user
    # An active region on the same host but another port is another server.
    inactive_other_port = _region("own.test", is_active=False, port=5433)
    manager = postgres_manager_for_key(
        Mock(database_host=None), inactive_other_port, regions_by_host
    )
    assert manager.admin_user == inactive_other_port.postgres_admin_user
    assert manager.port == 5433


def test_regions_by_postgres_host_prefers_active_on_shared_host():
    inactive = _region("shared.test", is_active=False)
    active = _region("shared.test")
    for order in ([inactive, active], [active, inactive]):
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = order
        assert regions_by_postgres_host(db) == {"shared.test": active}
