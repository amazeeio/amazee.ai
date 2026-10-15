import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _columns(connection, table="registry_models"):
    return {c["name"]: c for c in inspect(connection).get_columns(table)}


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


def test_aliases_migration_drops_and_adds_its_table(db):
    migration = importlib.import_module("app.migrations.versions.20261015_110000_c4a9e6f2d817_add_registry_aliases")
    connection = db.connection()
    try:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            assert "registry_aliases" not in inspect(connection).get_table_names()

            migration.upgrade()
            inspector = inspect(connection)
            columns = _columns(connection, "registry_aliases")
            assert set(columns) == {"id", "region_id", "alias", "target", "hidden", "created_at", "updated_at"}
            assert columns["hidden"]["nullable"] is False
            assert "false" in str(columns["hidden"]["default"]).lower()
            assert ["region_id", "alias"] in [c["column_names"] for c in inspector.get_unique_constraints("registry_aliases")]
            (foreign_key,) = inspector.get_foreign_keys("registry_aliases")
            assert foreign_key["referred_table"] == "regions"
            assert foreign_key["options"]["ondelete"] == "CASCADE"
    finally:
        db.rollback()
