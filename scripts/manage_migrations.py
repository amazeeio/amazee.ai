#!/usr/bin/env python3
import os
import sys

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import argparse
from alembic import command
from alembic.config import Config

def get_alembic_config():
    """Get the Alembic configuration."""
    config = Config(os.path.join(os.path.dirname(__file__), "..", "app", "migrations", "alembic.ini"))
    config.set_main_option("script_location", "app/migrations")
    return config

def create_migration(message):
    """Create a new migration."""
    config = get_alembic_config()
    command.revision(config, message=message, autogenerate=True)

def upgrade_db():
    """Upgrade database to the latest revision."""
    config = get_alembic_config()
    command.upgrade(config, "head")

def downgrade_db():
    """Downgrade database by one revision."""
    config = get_alembic_config()
    command.downgrade(config, "-1")

def main():
    parser = argparse.ArgumentParser(description="Manage database migrations")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Create migration
    create_parser = subparsers.add_parser("create", help="Create a new migration")
    create_parser.add_argument("message", help="Migration message")

    # Upgrade
    subparsers.add_parser("upgrade", help="Upgrade to the latest version")

    # Downgrade
    subparsers.add_parser("downgrade", help="Downgrade by one version")

    args = parser.parse_args()

    if args.command == "create":
        create_migration(args.message)
    elif args.command == "upgrade":
        upgrade_db()
    elif args.command == "downgrade":
        downgrade_db()
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()