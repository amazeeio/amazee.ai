import asyncpg
import re
import uuid
import logging
from sqlalchemy.orm import Session
from app.db.models import DBPrivateAIKey, DBRegion

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _validate_identifier(name: str, label: str = "identifier") -> None:
    """Raise ValueError if *name* is not a safe SQL identifier."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid {label} '{name}': only alphanumeric characters and underscores are allowed"
        )


def regions_by_postgres_host(db: Session) -> dict[str, DBRegion]:
    """Map each Postgres host to the region whose credentials reach it."""
    # A key's database can live on another region's host, and that region
    # holds the admin credentials for it. On a shared host, an active
    # region wins so a dead region's credentials are not used.
    regions_by_host = {}
    for region in db.query(DBRegion).filter(DBRegion.postgres_host.is_not(None)).all():
        current = regions_by_host.get(region.postgres_host)
        if current is None or (region.is_active and not current.is_active):
            regions_by_host[region.postgres_host] = region
    return regions_by_host


def postgres_manager_for_key(
    key: DBPrivateAIKey, key_region: DBRegion, regions_by_host: dict[str, DBRegion]
) -> "PostgresManager":
    """Return a manager for the key's own database host."""
    host = key.database_host or key_region.postgres_host
    # The region that owns the host holds its credentials. An inactive key
    # region gives way to an active region on the same host, so stale
    # credentials are not used. The key's region is the fallback when no
    # region owns the host.
    owner = (
        key_region
        if host == key_region.postgres_host and key_region.is_active
        else regions_by_host.get(host, key_region)
    )
    return PostgresManager(region=owner, host=host)


class PostgresManager:
    def __init__(self, region: DBRegion = None, host: str | None = None):
        if region:
            # A key's database can outlive a region's host change, so callers
            # may point at the key's own host with the region's credentials.
            self.host = host or region.postgres_host
            self.admin_user = region.postgres_admin_user
            self.admin_password = region.postgres_admin_password
            self.port = region.postgres_port
        else:
            raise ValueError("Region is required for PostgresManager")

    async def create_database(self) -> dict:
        # Generate unique database name and credentials
        db_name = f"db_{uuid.uuid4().hex[:8]}"
        db_user = f"user_{uuid.uuid4().hex[:8]}"
        db_password = uuid.uuid4().hex

        # Connect to postgres and create database/user
        try:
            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password,
            )
            logger.info("Successfully connected to PostgreSQL as admin user")
        except asyncpg.exceptions.PostgresError as e:
            logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
            logger.error(
                f"Connection details: host={self.host}, port={self.port}, user={self.admin_user}"
            )
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting to PostgreSQL: {str(e)}")
            raise

        try:
            logger.info(f"Creating database {db_name} and user {db_user}")
            await conn.execute(f"CREATE DATABASE {db_name}")
            await conn.execute(f"CREATE USER {db_user} WITH PASSWORD '{db_password}'")
            await conn.execute(
                f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}"
            )
            # PostgreSQL grants CONNECT to PUBLIC on every new database, and
            # every tenant role is a member of PUBLIC — so on a shared cluster
            # that default lets any tenant role open a session against any
            # other tenant's database. The GRANT ALL above already gave db_user
            # its own CONNECT, so revoking PUBLIC leaves exactly one role able
            # to connect.
            await conn.execute(f"REVOKE CONNECT ON DATABASE {db_name} FROM PUBLIC")
            logger.info("Database and user created successfully")

            # Close the initial connection
            await conn.close()

            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password,
                database=db_name,
            )

            try:
                # Grant schema permissions
                logger.info("Granting schema permissions")
                await conn.execute(f"GRANT ALL ON SCHEMA public TO {db_user}")
                await conn.execute(f"ALTER SCHEMA public OWNER TO {db_user}")
                await conn.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {db_user}"
                )
                await conn.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {db_user}"
                )
                await conn.execute(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO {db_user}"
                )
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                # On PostgreSQL < 15 PUBLIC also holds CREATE on the public
                # schema. db_user owns it (above) and is the only role that
                # should touch it. No app path connects to a tenant database as
                # the admin user, so this cannot lock our own tooling out.
                await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                logger.info("Schema permissions granted successfully")
            finally:
                await conn.close()

            return {
                "database_name": db_name,
                "database_username": db_user,
                "database_password": db_password,
                "database_host": self.host,
            }
        except Exception as e:
            logger.error(f"Error creating database: {str(e)}")
            raise
        finally:
            await conn.close()

    async def restrict_connect_to_owner(
        self, database_name: str, database_username: str
    ) -> None:
        """Make *database_name* connectable only by *database_username*.

        Databases provisioned before create_database started revoking it still
        carry PostgreSQL's default ``CONNECT`` grant to PUBLIC. Grant first,
        revoke second, so the owning tenant is never locked out even if the run
        is interrupted.
        """
        _validate_identifier(database_name, "database name")
        _validate_identifier(database_username, "database username")

        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
        )
        try:
            await conn.execute(
                f"GRANT CONNECT ON DATABASE {database_name} TO {database_username}"
            )
            await conn.execute(
                f"REVOKE CONNECT ON DATABASE {database_name} FROM PUBLIC"
            )
        finally:
            await conn.close()

    async def list_tenant_databases(self) -> list[str]:
        """Return every ``db_*`` database on the cluster.

        Used by the connect-privilege backfill to spot tenant databases the
        platform no longer tracks (failed deletes) — those keep PUBLIC
        connectivity and need an operator decision, so they are reported rather
        than modified.
        """
        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
        )
        try:
            rows = await conn.fetch(
                "SELECT datname FROM pg_database WHERE datname LIKE 'db\\_%'"
            )
            return [row["datname"] for row in rows]
        finally:
            await conn.close()

    async def delete_database(
        self, database_name: str, database_username: str | None = None
    ):
        _validate_identifier(database_name, "database name")
        if database_username:
            _validate_identifier(database_username, "database username")

        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password,
        )

        try:
            # Terminate all connections to the database (parameterized to prevent injection)
            await conn.execute(
                """
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = $1
                """,
                database_name,
            )
            await conn.execute(f"DROP DATABASE IF EXISTS {database_name}")
            if database_username:
                await conn.execute(
                    """
                    SELECT pg_terminate_backend(pg_stat_activity.pid)
                    FROM pg_stat_activity
                    WHERE pg_stat_activity.usename = $1
                    """,
                    database_username,
                )
                await conn.execute(f"DROP USER IF EXISTS {database_username}")
        finally:
            await conn.close()
