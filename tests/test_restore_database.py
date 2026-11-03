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


@patch("scripts.restore_database.recreate_target_db")
@patch("scripts.restore_database.run_pg_tool")
def test_apply_restore_in_place_never_drops_or_creates(mock_run_pg_tool, mock_recreate):
    from scripts.restore_database import apply_restore

    config = {
        "database": "my_db",
        "url": "postgres://host/my_db",
        "maintenance_url": "postgres://host/postgres",
    }
    apply_restore(config, "/mock/dump", restore_mode="in-place")

    mock_recreate.assert_not_called()
    cmd = mock_run_pg_tool.call_args.args[0]
    assert cmd[0] == "pg_restore"
    assert "--clean" in cmd
    assert "--create" not in cmd
    assert cmd[cmd.index("--dbname") + 1] == "postgres://host/my_db"
