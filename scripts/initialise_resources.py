#!/usr/bin/env python3

import os
import sys

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alembic.command
import alembic.config
import glob
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.limit_service import setup_default_limits
from app.core.security import get_password_hash
from app.db.database import engine
from app.db.models import Base, DBUser
from app.services.ses import SESService
from scripts.migrate_pricing_tables import migrate_pricing_tables


def verify_schema_matches_models() -> None:
    """Ensure DB schema includes all SQLAlchemy model tables/columns."""
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names())

    missing_tables = []
    missing_columns = []

    for table in Base.metadata.sorted_tables:
        table_name = table.name
        if table_name not in db_tables:
            missing_tables.append(table_name)
            continue

        db_columns = {col["name"] for col in inspector.get_columns(table_name)}
        model_columns = set(table.columns.keys())
        for col_name in sorted(model_columns - db_columns):
            missing_columns.append(f"{table_name}.{col_name}")

    if missing_tables or missing_columns:
        details = []
        if missing_tables:
            details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
        if missing_columns:
            details.append(f"missing columns: {', '.join(missing_columns)}")
        raise RuntimeError("Schema verification failed - " + "; ".join(details))


def init_database() -> Session:
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    print(f"Existing tables: {existing_tables}")

    alembic_cfg = alembic.config.Config(
        os.path.join(
            os.path.dirname(__file__), "..", "app", "migrations", "alembic.ini"
        )
    )
    alembic_cfg.set_main_option("script_location", "app/migrations")

    if "alembic_version" not in existing_tables:
        alembic.command.ensure_version(alembic_cfg)

    if not existing_tables:
        print("No tables found - creating database schema from models...")
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
        alembic.command.stamp(alembic_cfg, "head")
        print("Stamped alembic version for future migrations")
    else:
        print("Tables already exist - running migrations...")
        alembic.command.upgrade(alembic_cfg, "head")
        print("Database migrations completed successfully!")

    print("Verifying schema against models...")
    verify_schema_matches_models()
    print("Schema verification passed!")

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        admin_exists = db.query(DBUser).filter(DBUser.is_admin.is_(True)).first()
        if not admin_exists:
            print("Creating initial admin user...")
            admin_user = DBUser(
                email="admin@example.com",
                hashed_password=get_password_hash("admin"),
                is_active=True,
                is_admin=True,
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
        return db


def init_ses_templates():
    if os.getenv("PASSWORDLESS_SIGN_IN", "").lower() == "true":
        print("Initializing SES email templates...")
        ses_service = SESService()
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "templates"
        )

        template_files = glob.glob(os.path.join(templates_dir, "*.md"))
        for template_file in template_files:
            template_name = os.path.splitext(os.path.basename(template_file))[0]
            if ses_service.create_or_update_template(template_name):
                print(f"Successfully created/updated SES template: {template_name}")
            else:
                print(f"Failed to create/update SES template: {template_name}")
    else:
        print("PASSWORDLESS_SIGN_IN is disabled - skipping SES initialization")


def init_pricing_table_migration(db: Session):
    """Initialize pricing table data migration"""
    try:
        print("Initializing pricing table data migration...")
        success = migrate_pricing_tables(db)
        if success:
            print("Pricing table migration completed successfully")
        else:
            print(
                "Warning: Pricing table migration failed - continuing with initialization"
            )
    except Exception as e:
        print(
            f"Warning: Error during pricing table migration: {str(e)} - continuing with initialization"
        )


def init_default_limits(db: Session):
    """Initialize default system limits"""
    try:
        print("Initializing default system limits...")
        setup_default_limits(db)
        print("Default system limits initialized successfully")
    except Exception as e:
        print(
            f"Warning: Error during default limits initialization: {str(e)} - continuing with initialization"
        )


def main():
    try:
        print(
            f"Initialising resources for environment: {os.getenv('ENV_SUFFIX', 'local')}"
        )
        print(f"Main route: {settings.main_route}")
        db = init_database()
        init_ses_templates()
        init_pricing_table_migration(db)
        init_default_limits(db)
        db.close()
    except Exception as e:
        print(f"Error during initialization: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
