#!/usr/bin/env python3

import os
import sys
from datetime import datetime, UTC

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy.orm import sessionmaker, Session
from app.db.database import engine
from app.db.models import DBSystemSecret, DBPricingTable
from app.core.config import settings

def migrate_pricing_tables(db: Session) -> bool:
    """
    Migrate existing DBSystemSecret pricing table records to DBPricingTable format.

    Returns:
        bool: True if migration was successful or no migration needed, False if failed
    """
    print("Starting pricing table data migration...")

    try:
        # Check if pricing_tables table exists
        inspector = db.bind.dialect.inspector(db.bind)
        existing_tables = inspector.get_table_names()

        if 'pricing_tables' not in existing_tables:
            print("pricing_tables table does not exist yet - skipping migration")
            return True

        # Check if we already have any DBPricingTable records
        existing_new_tables = db.query(DBPricingTable).count()
        if existing_new_tables > 0:
            print(f"Found {existing_new_tables} existing DBPricingTable records - skipping migration")
            return True

        # Get existing system secrets
        standard_secret = db.query(DBSystemSecret).filter(
            DBSystemSecret.key == "CurrentPricingTable"
        ).first()

        always_free_secret = db.query(DBSystemSecret).filter(
            DBSystemSecret.key == "AlwaysFreePricingTable"
        ).first()

        migrated_count = 0

        # Migrate standard pricing table
        if standard_secret:
            print(f"Migrating standard pricing table: {standard_secret.value}")
            standard_table = DBPricingTable(
                table_type="standard",
                pricing_table_id=standard_secret.value,
                stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
                is_active=True,
                created_at=standard_secret.created_at or datetime.now(UTC),
                updated_at=standard_secret.updated_at
            )
            db.add(standard_table)
            migrated_count += 1
        else:
            print("No standard pricing table found in system secrets")

        # Migrate always-free pricing table
        if always_free_secret:
            print(f"Migrating always-free pricing table: {always_free_secret.value}")
            always_free_table = DBPricingTable(
                table_type="always_free",
                pricing_table_id=always_free_secret.value,
                stripe_publishable_key=settings.STRIPE_PUBLISHABLE_KEY,
                is_active=True,
                created_at=always_free_secret.created_at or datetime.now(UTC),
                updated_at=always_free_secret.updated_at
            )
            db.add(always_free_table)
            migrated_count += 1
        else:
            print("No always-free pricing table found in system secrets")

        if migrated_count > 0:
            db.commit()
            print(f"Successfully migrated {migrated_count} pricing table(s)")
        else:
            print("No pricing tables to migrate")

        return True

    except Exception as e:
        print(f"Error during pricing table migration: {str(e)}")
        db.rollback()
        return False

def main():
    """Main function to run the pricing table migration"""
    try:
        print("Starting pricing table data migration script...")

        # Create database session
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        try:
            success = migrate_pricing_tables(db)
            if success:
                print("Pricing table migration completed successfully")
                sys.exit(0)
            else:
                print("Pricing table migration failed")
                sys.exit(1)
        finally:
            db.close()

    except Exception as e:
        print(f"Error during migration script execution: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
