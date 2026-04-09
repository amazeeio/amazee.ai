import pytest
from app.db.postgres import _validate_identifier


@pytest.mark.parametrize(
    "name",
    [
        "db_abc123",
        "user_1a2b3c4d",
        "mydb",
        "MYDB",
        "db_00000000",
        "a",
        "_leading_underscore",
    ],
)
def test_validate_identifier_accepts_valid_names(name):
    # Should not raise
    _validate_identifier(name)


@pytest.mark.parametrize(
    "name",
    [
        "db-name",  # hyphen
        "db name",  # space
        "db'name",  # single quote (SQL injection vector)
        'db"name',  # double quote
        "db;DROP TABLE x",  # semicolon injection
        "db\x00name",  # null byte
        "",  # empty string
        "db/name",  # slash
        "db.name",  # dot
    ],
)
def test_validate_identifier_rejects_unsafe_names(name):
    with pytest.raises(
        ValueError, match="only alphanumeric characters and underscores"
    ):
        _validate_identifier(name)


def test_validate_identifier_uses_provided_label():
    with pytest.raises(ValueError, match="database name"):
        _validate_identifier("bad-name", "database name")
