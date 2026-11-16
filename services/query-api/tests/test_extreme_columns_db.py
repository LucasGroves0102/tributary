"""The extremes report (migration 072, ops.refresh_extremes) ranks the screener's columns: its SQL
list (ops.extreme_columns, from silver.metrics and gold.derived_metrics) is the formula CATALOG's,
column for column. Skipped unless TEST_DATABASE_URL is set."""

import os

import pytest

from query_api.screener import COLUMNS

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def test_the_reports_columns_are_the_screeners():
    with psycopg.connect(DSN) as conn:
        rows = conn.execute("SELECT column_key, metric, span FROM ops.extreme_columns()").fetchall()
    assert {k: (m, s) for k, m, s in rows} == {k: (m, s) for k, (m, s, _) in COLUMNS.items()}
    assert len(rows) == len(COLUMNS)
