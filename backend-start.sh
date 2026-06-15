#!/bin/bash
set -euo pipefail

# Build DATABASE_URL from Lagoon-injected POSTGRES_* vars if not already set.
# On Lagoon (DBaaS), DATABASE_URL is not injected directly — only the individual
# POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DATABASE / POSTGRES_USERNAME / POSTGRES_PASSWORD
# vars are available. The Python default falls back to @postgres which does not
# resolve in the DBaaS network, causing startup failure.
if [ -z "${DATABASE_URL:-}" ] && [ -n "${POSTGRES_HOST:-}" ]; then
    export DATABASE_URL="postgresql://${POSTGRES_USERNAME}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT:-5432}/${POSTGRES_DATABASE}"
    echo "DATABASE_URL built from POSTGRES_* vars: postgresql://${POSTGRES_USERNAME}:***@${POSTGRES_HOST}:${POSTGRES_PORT:-5432}/${POSTGRES_DATABASE}"
fi

# Wait for PostgreSQL to be ready and create database if needed
echo "Waiting for PostgreSQL to be ready..."
python /app/scripts/wait_for_database.py

if [ $? -ne 0 ]; then
    echo "Failed to connect to database"
    exit 1
fi

echo "PostgreSQL is up - initializing database"

# Initialize the database using the Python script
python /app/scripts/initialise_resources.py

# Check if running in production (Lagoon) or development mode
if [ -n "${LAGOON_ENVIRONMENT:-}" ]; then
    # Production mode (Lagoon)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --workers 4
else
    # Development mode (local docker-compose)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --reload
fi
