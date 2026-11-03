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


CONFIG = {
    "database": "my_db",
    "url": "postgres://host/my_db",
    "maintenance_url": "postgres://host/postgres",
}


@patch("scripts.restore_database.recreate_target_db")
@patch("scripts.restore_database.run_pg_tool")
def test_apply_restore_in_place_never_drops_or_creates(mock_run_pg_tool, mock_recreate):
    from scripts.restore_database import apply_restore

    apply_restore(CONFIG, "/mock/dump", restore_mode="in-place")

    mock_recreate.assert_not_called()
    reset, restore = [c.args[0] for c in mock_run_pg_tool.call_args_list]
    # Schema reset first, against the target DB, so tables the dump
    # doesn't know about can't block the restore.
    assert reset[0] == "psql"
    assert reset[reset.index("--dbname") + 1] == "postgres://host/my_db"
    assert "DROP SCHEMA public CASCADE" in reset[-1]
    assert restore[0] == "pg_restore"
    assert "--create" not in restore
    assert restore[restore.index("--dbname") + 1] == "postgres://host/my_db"


@patch("scripts.restore_database.run_pg_tool")
def test_apply_restore_in_place_recovery_hint_has_no_create(mock_run_pg_tool):
    import subprocess
    import pytest
    from scripts.restore_database import apply_restore

    mock_run_pg_tool.side_effect = [
        MagicMock(),
        subprocess.CalledProcessError(1, "pg_restore", stderr="boom"),
    ]
    with pytest.raises(SystemExit) as exc:
        apply_restore(CONFIG, "/mock/dump", restore_mode="in-place")
    assert "--create" not in str(exc.value)
    assert "/postgres" not in str(exc.value)


def _run_main(tmp_path, argv, backup_side_effect=None):
    """Run main() in in-place mode with a source DB name that differs from target."""
    from scripts import restore_database as rd

    backup = tmp_path / "b.tar.gz"
    backup.write_bytes(b"")
    with (
        patch.object(rd.sys, "argv", ["restore", str(backup), *argv]),
        patch.object(rd, "check_required_tools"),
        patch.object(rd, "check_disk_space", return_value=True),
        patch.object(rd, "get_db_config", return_value=dict(CONFIG)),
        patch.object(rd, "extract_backup", return_value=str(tmp_path)),
        patch.object(rd, "detect_database_name", return_value="other_db"),
        patch.object(rd, "run_pg_tool") as run_pg_tool,
        patch.object(
            rd, "backup_current_database", side_effect=backup_side_effect
        ) as backup_db,
        patch.object(rd, "apply_restore") as apply_restore,
        patch("builtins.input", return_value="yes"),
    ):
        try:
            rd.main()
        except SystemExit as e:
            return e.code, run_pg_tool, backup_db, apply_restore
    return None, run_pg_tool, backup_db, apply_restore


def test_main_in_place_keeps_mode_and_backs_up_target(tmp_path):
    code, run_pg_tool, backup_db, apply_restore = _run_main(
        tmp_path, ["--restore-mode", "in-place"]
    )

    assert code is None
    # No 1/2 prompt flipped the mode, despite differing source/target names.
    assert apply_restore.call_args.kwargs["restore_mode"] == "in-place"
    # Backs up the target, never the source, without the maintenance DB check.
    assert backup_db.call_args.args[0]["database"] == "my_db"
    run_pg_tool.assert_not_called()


def test_main_in_place_yes_aborts_when_safety_backup_fails(tmp_path):
    code, _, _, apply_restore = _run_main(
        tmp_path,
        ["--restore-mode", "in-place", "--yes"],
        backup_side_effect=SystemExit("pg_dump failed"),
    )

    assert code == 1
    apply_restore.assert_not_called()
