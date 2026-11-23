import json
import logging
import sys

from app.json_log import JsonFormatter


def test_json_formatter_keeps_the_traceback_in_one_line():
    try:
        raise ValueError("multi\nline")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "app.test", logging.ERROR, __file__, 1, "failed for %s", ("team-1",), exc_info
    )

    line = JsonFormatter().format(record)

    assert "\n" not in line
    entry = json.loads(line)
    assert entry["level"] == "error"
    assert entry["logger"] == "app.test"
    assert entry["message"] == "failed for team-1"
    assert "ValueError: multi\nline" in entry["exception"]


def test_log_config_writes_app_and_access_lines_as_json(capsys):
    import logging.config
    from pathlib import Path

    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        config = json.loads(
            (Path(__file__).parent.parent / "app" / "log_config.json").read_text()
        )
        logging.config.dictConfig(config)
        import app.main  # noqa: F401  its basicConfig must not override the config

        logging.getLogger("app.main").info("app line")
        logging.getLogger("uvicorn.access").info("access line")

        lines = capsys.readouterr().out.strip().splitlines()
        entries = [json.loads(line) for line in lines]
        assert [(e["logger"], e["message"]) for e in entries] == [
            ("app.main", "app line"),
            ("uvicorn.access", "access line"),
        ]
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
