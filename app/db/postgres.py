import asyncpg
from typing import Dict
import uuid
from app.services.litellm import LiteLLMService
from app.db.models import DBRegion

class PostgresManager:
    def __init__(self, region: DBRegion = None):
        if region:
            self.host = region.postgres_host
            self.admin_user = region.postgres_admin_user
            self.admin_password = region.postgres_admin_password
            self.port = region.postgres_port
            self.litellm_service = LiteLLMService(
                api_url=region.litellm_api_url,
                api_key=region.litellm_api_key
            )
        else:
            raise ValueError("Region is required for PostgresManager")

    async def create_database(self, owner: str, name: str = None, user_id: int = None) -> Dict:
        # Generate unique database name and credentials
        db_name = f"db_{uuid.uuid4().hex[:8]}"
        db_user = f"user_{uuid.uuid4().hex[:8]}"
        db_password = uuid.uuid4().hex
        
        # Generate LiteLLM token
        litellm_token = await self.litellm_service.create_key(email=owner, name=name, user_id=user_id)

        # Connect to postgres and create database/user
        try:
            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password
            )
        except asyncpg.exceptions.PostgresError as e:
            print(f"Failed to connect to PostgreSQL: {str(e)}")
            print(f"Connection details: host={self.host}, port={self.port}, user={self.admin_user}")
            raise
        except Exception as e:
            print(f"Unexpected error connecting to PostgreSQL: {str(e)}")
            raise

        try:
            await conn.execute(f'CREATE DATABASE {db_name}')
            await conn.execute(f'CREATE USER {db_user} WITH PASSWORD \'{db_password}\'')
            await conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}')

            # Close the initial connection
            await conn.close()

            # Connect to the newly created database to grant schema permissions
            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password,
                database=db_name
            )

            try:
                # Grant schema permissions
                await conn.execute(f'GRANT ALL ON SCHEMA public TO {db_user}')
                await conn.execute(f'ALTER SCHEMA public OWNER TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {db_user}')
                await conn.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO {db_user}')
            finally:
                await conn.close()

            return {
                "database_name": db_name,
                "database_username": db_user,
                "database_password": db_password,
                "database_host": self.host,
                "litellm_token": litellm_token,
                "litellm_api_url": self.litellm_service.api_url
            }
        except Exception as e:
            print(f"Error creating database: {str(e)}")
            raise

    async def delete_database(self, database_name: str, litellm_token: str = None):
        conn = await asyncpg.connect(
            host=self.host,
            port=self.port,
            user=self.admin_user,
            password=self.admin_password
        )

        try:
            # Delete LiteLLM key if provided
            if litellm_token:
                await self.litellm_service.delete_key(litellm_token)

            # Terminate all connections to the database
            await conn.execute(f'''
                SELECT pg_terminate_backend(pg_stat_activity.pid)
                FROM pg_stat_activity
                WHERE pg_stat_activity.datname = '{database_name}'
            ''')
            await conn.execute(f'DROP DATABASE IF EXISTS {database_name}')
        finally:
            await conn.close()