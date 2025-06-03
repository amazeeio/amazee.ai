#!/usr/bin/env python3

import os
import sys

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import alembic.config
import alembic.command
import asyncio
import glob
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker, Session
from app.db.database import engine
from app.db.models import Base, DBUser
from app.core.security import get_password_hash
from app.services.ses import SESService
from app.services.stripe import setup_stripe_webhook
from app.api.billing import BILLING_WEBHOOK_KEY, BILLING_WEBHOOK_ROUTE

def init_database() -> Session:
    # Check if database is empty (no tables exist)
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()
    print(f"Existing tables: {existing_tables}")

    alembic_cfg = alembic.config.Config(os.path.join(os.path.dirname(__file__), "..", "app", "migrations", "alembic.ini"))
    alembic_cfg.set_main_option("script_location", "app/migrations")

    if not "alembic_version" in existing_tables:
        alembic.command.ensure_version(alembic_cfg)

    if not existing_tables:
        print("No tables found - creating database schema from models...")
        # Create all tables from models
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
        alembic.command.stamp(alembic_cfg, "head")
        print("Stamped alembic version for future migrations")
    else:
        print("Tables already exist - running migrations...")
        # Run database migrations
        try:
            alembic.command.upgrade(alembic_cfg, "head")
            print("Database migrations completed successfully!")
        except Exception as e:
            print(f"Migration failed: {str(e)}")
            print("Attempting to stamp current version and continue...")
            # If migration fails, stamp the current version
            alembic.command.stamp(alembic_cfg, "head")
            print("Database version stamped successfully!")

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
        return db

def init_webhooks(db: Session):
    # Only set up Stripe webhook in specific environments
    env_suffix = os.getenv("ENV_SUFFIX", "").lower()
    if env_suffix in ["dev", "main", "prod"]:
        try:
            # Set up Stripe webhook
            print("Setting up Stripe Billing webhook...")
            asyncio.run(setup_stripe_webhook(BILLING_WEBHOOK_KEY, BILLING_WEBHOOK_ROUTE, db))
            print("Stripe Billing webhook set up successfully")
        except Exception as e:
            print(f"Warning: Failed to set up Stripe Billing webhook: {str(e)}")
    else:
        print(f"Skipping Stripe webhook setup for environment: {env_suffix}")

def init_ses_templates():
    if os.getenv("PASSWORDLESS_SIGN_IN", "").lower() == "true":
        # Initialize SES templates
        print("Initializing SES email templates...")
        ses_service = SESService()
        templates_dir = os.path.join(os.path.dirname(__file__), "..", "app", "templates")

        # Get all .md files in the templates directory
        template_files = glob.glob(os.path.join(templates_dir, "*.md"))

        for template_file in template_files:
            template_name = os.path.splitext(os.path.basename(template_file))[0]
            if ses_service.create_or_update_template(template_name):
                print(f"Successfully created/updated SES template: {template_name}")
            else:
                print(f"Failed to create/update SES template: {template_name}")
    else:
        print("PASSWORDLESS_SIGN_IN is disabled - skipping SES initialization")

def main():
    try:
        db = init_database()
        init_webhooks(db)
        init_ses_templates()
        db.close()
    except Exception as e:
        print(f"Error during initialization: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()