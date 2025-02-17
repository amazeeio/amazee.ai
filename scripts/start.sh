#!/bin/bash

# Function to test database connection
postgres_ready() {
python << END
import sys
import psycopg2
import os
try:
    psycopg2.connect(
        dbname=os.getenv("POSTGRES_DB", "postgres_service"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        host=os.getenv("POSTGRES_HOST", "postgres")
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

print("Creating database tables...")
# Create all tables
Base.metadata.create_all(bind=engine)
print("Database tables created successfully!")

# Create initial admin user if none exists
from sqlalchemy.orm import sessionmaker
from app.db.models import DBUser
from app.core.security import get_password_hash
import os

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

# Start the FastAPI application
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload