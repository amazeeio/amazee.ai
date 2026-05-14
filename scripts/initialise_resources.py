#!/usr/bin/env python3

import os
import sys

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import alembic.config
import alembic.command
import asyncio
import glob
from sqlalchemy import inspect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.orm import sessionmaker, Session
from app.db.database import engine
from app.db.models import Base, DBUser
from app.core.config import settings
from app.core.security import get_password_hash
from app.services.ses import SESService
from app.services.stripe import setup_stripe_webhook
from app.api.billing import BILLING_WEBHOOK_KEY, BILLING_WEBHOOK_ROUTE
from scripts.migrate_pricing_tables import migrate_pricing_tables
from app.core.limit_service import setup_default_limits


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


def _restamp_to_safe_revision(
    alembic_cfg: alembic.config.Config, inspector: Inspector
) -> None:
    """Re-stamp alembic to the ``down_revision`` of the first migration
    (walking from head backwards) whose schema effects are missing.

    This handles the case where ``alembic_version`` was stamped past
    migrations that never actually ran (e.g. a ``create_all`` +
    ``stamp('head')`` on a previous deploy that missed later migrations).
    """
    from alembic.script import ScriptDirectory
    import pathlib

    script = ScriptDirectory.from_config(alembic_cfg)
    db_tables = set(inspector.get_table_names())
    db_columns_cache: dict[str, set[str]] = {}

    def _get_columns(table_name: str) -> set[str]:
        if table_name not in db_columns_cache:
            db_columns_cache[table_name] = {
                col["name"] for col in inspector.get_columns(table_name)
            }
        return db_columns_cache[table_name]

    # Walk linearly from head backwards.  Find the first revision (from
    # head) whose schema effects are NOT present.  Stamp to its
    # down_revision so ``upgrade head`` can replay it and everything after.
    revision = script.get_current_head()
    if revision is None:
        print("Could not determine alembic head revision - skipping re-stamp")
        return

    while revision:
        rev_obj = script.get_revision(revision)
        if rev_obj is None:
            break

        try:
            source = pathlib.Path(rev_obj.module.__file__).read_text()
        except Exception:
            break

        if _migration_has_missing_ops(source, db_tables, _get_columns):
            # This revision hasn't been applied – stamp to its parent
            # so ``upgrade head`` will replay from there.
            down = rev_obj.down_revision
            if isinstance(down, tuple):
                down = down[0]
            stamp_target = down or "base"
            print(f"Re-stamping alembic to revision: {stamp_target}")
            alembic.command.stamp(alembic_cfg, stamp_target)
            return

        # This revision is applied – keep walking back
        down = rev_obj.down_revision
        if isinstance(down, tuple):
            down = down[0]
        revision = down

    # If we get here, all revisions look applied but schema still fails.
    # Fall back to base to let alembic replay everything.
    print(
        "All migrations appear applied but schema still has gaps - re-stamping to base"
    )
    alembic.command.stamp(alembic_cfg, "base")


def _migration_has_missing_ops(source: str, db_tables: set, get_columns) -> bool:
    """Return True when migration schema operations appear unapplied."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue

        if func.attr == "create_table":
            # First positional arg is the table name
            if node.args and isinstance(node.args[0], ast.Constant):
                if node.args[0].value not in db_tables:
                    return True

        elif func.attr == "add_column":
            # First arg = table name, second arg = Column with name
            if (
                len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[1], ast.Call)
            ):
                table_name = node.args[0].value
                col_arg = node.args[1]
                if (
                    table_name in db_tables
                    and isinstance(col_arg.func, ast.Attribute)
                    and col_arg.func.attr == "Column"
                    and col_arg.args
                    and isinstance(col_arg.args[0], ast.Constant)
                ):
                    col_name = col_arg.args[0].value
                    if col_name not in get_columns(table_name):
                        return True

        elif func.attr == "rename_table":
            if (
                len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[1], ast.Constant)
            ):
                old_name = node.args[0].value
                new_name = node.args[1].value
                if old_name in db_tables and new_name not in db_tables:
                    return True

        elif func.attr == "alter_column":
            if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant):
                table_name = node.args[0].value
                if not isinstance(node.args[1], ast.Constant):
                    continue
                old_col_name = node.args[1].value
                new_col_name = None
                for kwarg in node.keywords:
                    if kwarg.arg == "new_column_name" and isinstance(
                        kwarg.value, ast.Constant
                    ):
                        new_col_name = kwarg.value.value
                        break

                if new_col_name and table_name in db_tables:
                    existing_columns = get_columns(table_name)
                    if (
                        old_col_name in existing_columns
                        and new_col_name not in existing_columns
                    ):
                        return True

    return False


def init_database() -> Session:
    # Check if database is empty (no tables exist)
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
        # Create all tables from models
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully!")
        alembic.command.stamp(alembic_cfg, "head")
        print("Stamped alembic version for future migrations")
    else:
        print("Tables already exist - running migrations...")

        # Guard against stale alembic stamps: if the DB reports it is at
        # head but the actual schema is missing tables/columns, re-stamp
        # to a safe point so ``upgrade head`` can replay missing migrations.
        # We walk backwards from head, checking each revision's upgrade()
        # effects until we find one that has already been applied.
        try:
            verify_schema_matches_models()
        except RuntimeError as exc:
            print(
                f"Schema gap detected ({exc}) "
                f"- finding safe alembic stamp point to replay migrations"
            )
            _restamp_to_safe_revision(alembic_cfg, inspector)

        alembic.command.upgrade(alembic_cfg, "head")
        print("Database migrations completed successfully!")

    print("Verifying schema against models...")
    verify_schema_matches_models()
    print("Schema verification passed!")

    # Create initial admin user if none exists
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


def init_webhooks(db: Session):
    # Only set up Stripe webhook in specific environments
    env_suffix = os.getenv("ENV_SUFFIX", "").lower()
    if env_suffix in ["dev", "main", "prod"]:
        try:
            # Set up Stripe webhook
            print("Setting up Stripe Billing webhook...")
            asyncio.run(
                setup_stripe_webhook(BILLING_WEBHOOK_KEY, BILLING_WEBHOOK_ROUTE, db)
            )
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
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "app", "templates"
        )

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
        init_webhooks(db)
        init_ses_templates()
        init_pricing_table_migration(db)
        init_default_limits(db)
        db.close()
    except Exception as e:
        print(f"Error during initialization: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
