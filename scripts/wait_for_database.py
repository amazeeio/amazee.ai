#!/usr/bin/env python3
"""
Script to wait for PostgreSQL database to be ready and create it if it doesn't exist.
"""

import sys
import psycopg2
import os
from urllib.parse import urlparse
import time


def create_database_if_not_exists():
    """Attempt to connect to database and create it if it doesn't exist."""
    database_url = os.getenv("DATABASE_URL", "postgres://postgres:postgres@postgres:5432/postgres_service")

    # Parse the database URL to extract components
    parsed = urlparse(database_url)

    # Create connection URL without database name (to connect to postgres default database)
    base_url = f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port or 5432}/postgres"

    try:
        # First try to connect to the target database
        psycopg2.connect(database_url)
        print("Database connection successful")
        return True
    except psycopg2.OperationalError as e:
        # If database doesn't exist, try to create it
        if "does not exist" in str(e) or "database" in str(e).lower():
            print("Database does not exist, attempting to create it...")
            try:
                # Connect to postgres database to create the target database
                conn = psycopg2.connect(base_url)
                conn.autocommit = True
                cursor = conn.cursor()

                # Extract database name from the original URL
                db_name = parsed.path.lstrip('/')

                # Create the database
                cursor.execute(f"CREATE DATABASE {db_name}")
                cursor.close()
                conn.close()

                print(f"Database '{db_name}' created successfully")

                # Try connecting again to verify
                psycopg2.connect(database_url)
                print("Database connection verified after creation")
                return True
            except Exception as create_error:
                print(f"Failed to create database: {create_error}")
                return False
        else:
            # Other connection error (host unreachable, etc.)
            print(f"Database connection error: {e}")
            return False


def wait_for_database(max_retries=60, retry_interval=1):
    """Wait for database to be available with retries."""
    for attempt in range(max_retries):
        if create_database_if_not_exists():
            return True
        print(f"Database not ready, retrying in {retry_interval} seconds... (attempt {attempt + 1}/{max_retries})")
        time.sleep(retry_interval)

    print("Failed to connect to database after maximum retries")
    return False


if __name__ == "__main__":
    if wait_for_database():
        sys.exit(0)
    else:
        sys.exit(1)