"""JSON log lines on stdout, each carrying the current request ID."""

import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# LogRecord attributes that aren't `extra` fields.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class RequestIdFilter(logging.Filter):
    """Stamps each record with the request ID current when it was logged."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = _request_id.get()
        if rid is not None and not hasattr(record, "request_id"):
            record.request_id = rid
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, then any extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: str | None = None) -> None:
    """Root logger → JSON on stdout, at `level` or $LOG_LEVEL (default INFO)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    logging.basicConfig(level=level or os.environ.get("LOG_LEVEL", "INFO"),
                        handlers=[handler], force=True)


def valid_request_id(value: str | None) -> str:
    """`value` if it is a safe request ID (letters, digits, `._-`, up to 64), else a new one."""
    if value and _REQUEST_ID.fullmatch(value):
        return value
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def request_id(value: str | None = None) -> Iterator[str]:
    """Set the request ID (`value` if valid, else a new one) for this context."""
    rid = valid_request_id(value)
    token = _request_id.set(rid)
    try:
        yield rid
    finally:
        _request_id.reset(token)
