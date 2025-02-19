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

    async def create_database(self, owner: str, user_id: int) -> Dict:
        # Generate LiteLLM token
        print("Generating LiteLLM token...")
        litellm_token = await self.litellm_service.create_key(user_id)
        print("LiteLLM token generated successfully")

        print(f"Creating new database for owner: {owner}")

        # Connect to postgres and create database/user
        try:
            print(f"Attempting to connect to PostgreSQL at {self.host}:{self.port} as {self.admin_user}...")
            conn = await asyncpg.connect(
                host=self.host,
                port=self.port,
                user=self.admin_user,
                password=self.admin_password
            )
            print("Successfully connected to PostgreSQL")
        except asyncpg.exceptions.PostgresError as e:
            print(f"Failed to connect to PostgreSQL: {str(e)}")
            print(f"Connection details: host={self.host}, port={self.port}, user={self.admin_user}")
            raise
        except Exception as e:
            print(f"Unexpected error connecting to PostgreSQL: {str(e)}")
            raise

        # Generate unique database name and credentials
        db_name = f"db_{uuid.uuid4().hex[:8]}"
        db_user = f"user_{uuid.uuid4().hex[:8]}"
        db_password = uuid.uuid4().hex

        print(f"Generated credentials - DB: {db_name}, User: {db_user}")

        try:
            print("Creating database...")
            await conn.execute(f'CREATE DATABASE {db_name}')
            print("Creating user...")
            await conn.execute(f'CREATE USER {db_user} WITH PASSWORD \'{db_password}\'')
            print("Granting privileges...")
            await conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}')

            print("Database created successfully")
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
        finally:
            await conn.close()

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