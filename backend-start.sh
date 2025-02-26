#!/bin/bash

# Function to test database connection
postgres_ready() {
python << END
import sys
import psycopg2
import os
import alembic
import alembic.config
import alembic.command
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

# Initialize the database
python << END
from app.db.database import engine
from app.db.models import Base
from sqlalchemy import inspect
import alembic.config
import os
from sqlalchemy.orm import sessionmaker
from app.db.models import DBUser
from app.core.security import get_password_hash

# Check if database is empty (no tables exist)
inspector = inspect(engine)
existing_tables = inspector.get_table_names()
print(f"Existing tables: {existing_tables}")

if not existing_tables:
    print("No tables found - creating database schema from models...")
    # Create all tables from models
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully!")
else:
    print("Tables already exist - running migrations...")
    # Run database migrations
    alembic_cfg = alembic.config.Config(os.path.join(os.path.dirname(__file__), "app", "migrations", "alembic.ini"))
    alembic_cfg.set_main_option("script_location", "app/migrations")
    alembic.command.upgrade(alembic_cfg, "head")
    print("Database migrations completed successfully!")

# Create initial admin user if none exists
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()

try:
    admin_exists = db.query(DBUser).filter(DBUser.is_admin == True).first()
    if not admin_exists:
        print("Creating initial admin user...")
        admin_user = DBUser(
            email="admin@example.com",
            hashed_password=get_password_hash("admin"),
            is_active=True,
            is_admin=True
        )
        db.add(admin_user)
        db.commit()
        print("Initial admin user created with credentials:")
        print("Email: admin@example.com")
        print("Password: admin")
        print("Please change these credentials after first login!")
    else:
        print("Admin user already exists")
except Exception as e:
    print(f"Error creating admin user: {str(e)}")
    db.rollback()
finally:
    db.close()
END

# Check if running in production (Lagoon) or development mode
if [ -n "${LAGOON_ENVIRONMENT}" ]; then
    # Production mode (Lagoon)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --workers 4
else
    # Development mode (local docker-compose)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8800 --reload
fi