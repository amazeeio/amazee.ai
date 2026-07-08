#!/bin/bash
set -euo pipefail

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

# Which client IPs uvicorn trusts X-Forwarded-* headers from. Set
# FORWARDED_ALLOW_IPS to the ingress/router CIDR in production so clients
# can't spoof X-Forwarded-Proto/For.

# Check if running in production (Lagoon) or development mode
if [ -n "${LAGOON_ENVIRONMENT:-}" ]; then
    # Production default: trust only private (in-cluster) peers, never "*".
    FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
    # Production mode (Lagoon)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --workers 4 \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}"
else
    # Development mode (local docker-compose)
    FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --reload \
        --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}"
fi
