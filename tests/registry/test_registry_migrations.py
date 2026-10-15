import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _columns(connection):
    return {c["name"]: c for c in inspect(connection).get_columns("registry_models")}


def test_model_overrides_migration_drops_and_adds_its_columns(db):
    migration = importlib.import_module(
        "app.migrations.versions.20261015_100000_b8d2e5f1c374_add_registry_model_overrides"
    )
    connection = db.connection()
    try:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            columns = _columns(connection)
            assert "override_eol" not in columns
            assert "blocked" not in columns

            migration.upgrade()
            columns = _columns(connection)
            assert columns["override_eol"]["nullable"] is True
            assert columns["blocked"]["nullable"] is False
            assert "false" in str(columns["blocked"]["default"]).lower()
    finally:
        # Postgres DDL is part of the transaction, so the next tests keep the schema.
        db.rollback()
