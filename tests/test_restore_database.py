import os
from unittest.mock import patch, MagicMock
from scripts.restore_database import (
    validate_db_name,
    get_db_config,
    detect_database_name,
)


def test_validate_db_name():
    assert validate_db_name("my_db") is True
    assert validate_db_name("mydb123") is True
    assert validate_db_name("my_db_2") is True
    assert validate_db_name("my-db") is True
    assert validate_db_name("db; drop database") is False
    assert validate_db_name("") is False
    assert validate_db_name("../etc/passwd") is False


@patch.dict(os.environ, {"DATABASE_URL": "postgres://user:pass@host:5432/my_db"})
def test_get_db_config_strips_password():
    config = get_db_config()
    assert config["database"] == "my_db"
    assert "pass" not in config["url"]
    assert "pass" not in config["maintenance_url"]
    assert config["password"] == "pass"
    assert config["user"] == "user"
    assert config["host"] == "host"
    assert config["port"] == 5432


@patch("scripts.restore_database.run_pg_tool")
def test_detect_database_name(mock_run_pg_tool):
    config = {"url": "postgres://host/db"}

    # Test different whitespace styles in TOC headers
    headers = [
        ";     dbname: my_detected_db",
        "; dbname: my_detected_db",
        ";\tdbname: my_detected_db",
        ";      dbname: my_detected_db",
    ]

    for header in headers:
        mock_res = MagicMock()
        mock_res.stdout = f"{header}\n; other fields"
        mock_run_pg_tool.return_value = mock_res

        detected = detect_database_name("/mock/dump", config)
        assert detected == "my_detected_db", f"Failed for header: {header}"
