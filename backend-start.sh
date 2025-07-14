#!/bin/bash

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
if [ -n "${LAGOON_ENVIRONMENT}" ]; then
    # Production mode (Lagoon)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --workers 4
else
    # Development mode (local docker-compose)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --reload
fi