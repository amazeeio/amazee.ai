import asyncpg
import uuid
import logging
from app.db.models import DBRegion

logger = logging.getLogger(__name__)

class PostgresManager:
    def __init__(self, region: DBRegion = None):
        if region:
            self.host = region.postgres_host
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
                password=self.admin_password
            )
            logger.info("Successfully connected to PostgreSQL as admin user")
        except asyncpg.exceptions.PostgresError as e:
            logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
            logger.error(f"Connection details: host={self.host}, port={self.port}, user={self.admin_user}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting to PostgreSQL: {str(e)}")
            raise

        try:
            logger.info(f"Creating database {db_name} and user {db_user}")
            await conn.execute(f'CREATE DATABASE {db_name}')
            await conn.execute(f'CREATE USER {db_user} WITH PASSWORD \'{db_password}\'')
            await conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}')
            logger.info("Database and user created successfully")

            # Close the initial connection
            await conn.close()

            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password,
                database=db_name
            )

            try:
                # Grant schema permissions
                logger.info("Granting schema permissions")
                await conn.execute(f'GRANT ALL ON SCHEMA public TO {db_user}')
                await conn.execute(f'ALTER SCHEMA public OWNER TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO {db_user}')
                await conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
                logger.info("Schema permissions granted successfully")
            finally:
                await conn.close()

            return {
                "database_name": db_name,
                "database_username": db_user,
                "database_password": db_password,
                "database_host": self.host
            }
        except Exception as e:
            logger.error(f"Error creating database: {str(e)}")
            raise
        finally:
            await conn.close()

    async def delete_database(self, database_name: str):
        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password
        )

        try:
            # Terminate all connections to the database
            await conn.execute(f'''
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = '{database_name}'
            ''')
            await conn.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            await conn.close()
