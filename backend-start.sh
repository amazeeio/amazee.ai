#!/bin/bash

# Function to test database connection
postgres_ready() {
python << END
import sys
import psycopg2
import os
try:
    psycopg2.connect(
        os.getenv("DATABASE_URL", "postgres://postgres:postgres@postgres:5432/postgres_service")
    )
except psycopg2.OperationalError:
    sys.exit(-1)
sys.exit(0)
END
}

# Wait for PostgreSQL to be ready
until postgres_ready; do
  >&2 echo "PostgreSQL is unavailable - sleeping"
  sleep 1
done

>&2 echo "PostgreSQL is up - initializing database"

# Initialize the database using the Python script
python /app/scripts/initialise_resources.py

# Check if running in production (Lagoon) or development mode
if [ -n "${LAGOON_ENVIRONMENT}" ]; then
    # Production mode (Lagoon)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --workers 4
else
    # Development mode (local docker-compose)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --reload
fi