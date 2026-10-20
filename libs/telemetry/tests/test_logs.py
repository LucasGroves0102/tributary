import io
import json
import logging
import sys

from tributary_telemetry import configure_logging
from tributary_telemetry.logs import (
    JsonFormatter,
    RequestIdFilter,
    current_request_id,
    request_id,
)


def record(msg="fetched %s", args=("x",), exc_info=None, **extra):
    r = logging.LogRecord("doc_fetcher", logging.INFO, __file__, 1, msg, args, exc_info)
    r.__dict__.update(extra)
    return r


def test_json_line_has_level_logger_message_and_extras():
    line = json.loads(JsonFormatter().format(record(accession_no="0001045810-26-000075")))
    assert line["level"] == "INFO"
    assert line["logger"] == "doc_fetcher"
    assert line["message"] == "fetched x"
    assert line["accession_no"] == "0001045810-26-000075"
    assert line["ts"].endswith("+00:00")
    assert "request_id" not in line
    assert "args" not in line and "msecs" not in line


def test_filter_stamps_the_request_id_current_at_log_time():
    r = record()
    with request_id("abc-123"):
        RequestIdFilter().filter(r)
    assert json.loads(JsonFormatter().format(r))["request_id"] == "abc-123"


def test_exceptions_are_formatted():
    try:
        raise ValueError("bad")
    except ValueError:
        line = json.loads(JsonFormatter().format(record(exc_info=sys.exc_info())))
    assert "ValueError: bad" in line["exc"]


def test_configure_logging_writes_json_lines_to_stdout(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    root.handlers = []  # keep pytest's handlers out of basicConfig(force=True)
    try:
        configure_logging("INFO")
        with request_id("r1"):
            logging.getLogger("poller").info("published %d new filings", 3, extra={"count": 3})
    finally:
        root.handlers = handlers
        root.setLevel(level)
    line = json.loads(out.getvalue())
    assert line["message"] == "published 3 new filings"
    assert line["request_id"] == "r1" and line["count"] == 3


def test_request_id_is_kept_if_safe_and_replaced_otherwise():
    with request_id("req.1_A-b") as rid:
        assert rid == "req.1_A-b" == current_request_id()
    assert current_request_id() is None
    for unsafe in (None, "", "a b", "x\ny", "z" * 65):
        with request_id(unsafe) as rid:
            assert rid != unsafe and len(rid) == 32
