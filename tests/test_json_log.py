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
