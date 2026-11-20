"""The alerts job's summary line logs its counts without clashing with LogRecord's own fields."""

import io
import json
import logging

from query_api.alert_job import Report, log_report
from tributary_telemetry.logs import JsonFormatter


def test_summary_logs_every_count_without_overwriting_record_fields():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("query_api.alert_job")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        log_report(Report(users=3, created=2, delivered=1))  # `created` is also a LogRecord field
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["message"].startswith("alerts run: 3 users, 2 new")
    assert line["report"]["created"] == 2 and line["report"]["users"] == 3
