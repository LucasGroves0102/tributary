"""The catch-up's pure parts (filing_parser/catchup.py); the DB parts are in test_catchup_db.py."""

from filing_parser import catchup
from filing_parser.sections import PARSER_VERSION


def test_the_version_is_the_parsers_number():
    assert PARSER_VERSION == f"python-{catchup.parser_version()}"


def test_the_budget_keeps_the_hourly_job_inside_its_hour():
    assert catchup.budget(1200, 60) == 1200          # a quick run: the whole budget
    assert catchup.budget(1200, 2400) == 600         # a long one: what is left of 50 minutes
    assert catchup.budget(1200, 3600) == 0           # past it: nothing (measured only)
