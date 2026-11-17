"""The catch-up's pure parts (facts_fetcher/catchup.py); the DB parts are in test_catchup_db.py."""

from facts_fetcher import catchup


def test_the_budget_keeps_the_hourly_job_inside_its_hour():
    assert catchup.budget(1200, 60) == 1200          # a quick segments run: the whole budget
    assert catchup.budget(1200, 2400) == 600         # a long one: what is left of 50 minutes
    assert catchup.budget(1200, 3600) == 0           # past it: nothing (measured only)


def test_cheap_kinds_go_first():
    assert catchup.KINDS == ("check_segments", "check_financials", "gold", "known")
