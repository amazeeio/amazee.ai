import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_VERSIONS = Path(__file__).resolve().parents[2] / "app" / "migrations" / "versions"


def _load(name):
    # The file name starts with a digit, so a normal import cannot load it.
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection):
    return {c["name"]: c for c in inspect(connection).get_columns("registry_models")}


def test_model_overrides_migration_drops_and_adds_its_columns(db):
    migration = _load("20261015_100000_b8d2e5f1c374_add_registry_model_overrides")
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
