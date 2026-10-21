"""The sec_limiter group (db/migrations/022_sec_limiter_role.sql): a login in it can take SEC
tokens and report throttles, and can't read or write any table. Skipped unless
TEST_DATABASE_URL is set (a superuser, as in CI: the tests create a login role)."""

import os
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest

from tributary_sec import PostgresStore

psycopg = pytest.importorskip("psycopg")
from psycopg import errors, sql  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

FUNCTIONS = {"ops.sec_take_token(text,text)", "ops.sec_report_throttle(text)"}
SYSTEM = ("pg_catalog", "information_schema", "pg_toast")


@pytest.fixture
def login():
    """A committed login in sec_limiter, as the owner creates it on the host, plus a test
    bucket: (url, role, bucket). Both are dropped afterwards."""
    assert DSN
    role, password = f"sec_test_{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    bucket = f"test-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE sec_limiter").format(
            sql.Identifier(role), sql.Literal(password)))
        c.execute("INSERT INTO ops.sec_rate_limit (bucket, rate, capacity, tokens)"
                  " VALUES (%s, 1, 2, 2)", (bucket,))
        try:
            parts = urlsplit(DSN)
            netloc = f"{role}:{password}@{parts.netloc.rsplit('@', 1)[-1]}"
            yield urlunsplit(parts._replace(netloc=netloc)), role, bucket
        finally:
            c.execute("DELETE FROM ops.sec_rate_limit WHERE bucket = %s", (bucket,))
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_login_takes_tokens_and_reports_throttles(login):
    url, _, bucket = login
    store = PostgresStore(url, bucket=bucket)
    try:
        first = store.take("live")
        assert first.wait_s == 0 and first.granted == 1
        assert store.take("live").wait_s == 0
        assert store.take("live").wait_s > 0  # capacity 2: the bucket is empty
        assert store.report_throttle() == pytest.approx(10)
        assert store.take("live").cooling
    finally:
        store.close()


def test_login_cannot_read_or_write_any_table(login):
    url, role, _ = login
    with psycopg.connect(DSN or "") as c:
        granted = c.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname <> ALL(%s) AND n.nspname !~ '^pg_' AND CASE"
            "   WHEN c.relkind = 'S' THEN has_sequence_privilege(%s, c.oid,"
            "                               'USAGE, SELECT, UPDATE')"
            "   WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') THEN"
            "     has_table_privilege(%s, c.oid,"
            "                         'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES')"
            "     OR has_any_column_privilege(%s, c.oid, 'SELECT, INSERT, UPDATE')"
            "   ELSE false END",
            (list(SYSTEM), role, role, role)).fetchall()
        assert granted == []
        total = c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n"
                          " ON n.oid = c.relnamespace WHERE n.nspname IN ('ops', 'gold')"
                          " AND c.relkind IN ('r', 'v')").fetchone()
        assert total and total[0] > 10  # the check above really looked at the schemas

    with psycopg.connect(url, autocommit=True) as c:
        for statement in ("SELECT * FROM ops.sec_rate_limit",
                          "SELECT * FROM ops.sec_rate_limit_status",
                          "UPDATE ops.sec_rate_limit SET tokens = 100",
                          "SELECT * FROM gold.quarterly_financials",
                          "SELECT * FROM silver.filing_sections",
                          "SELECT * FROM bronze.filing_documents",
                          "SELECT * FROM public.schema_migrations"):
            with pytest.raises(errors.InsufficientPrivilege):
                c.execute(statement)


def test_only_the_two_functions_run_with_definer_rights(login):
    _, role, _ = login
    with psycopg.connect(DSN or "") as c:
        rows = c.execute(
            "SELECT p.oid::regprocedure::text, p.proconfig FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE p.prosecdef AND n.nspname <> ALL(%s)"
            "   AND has_function_privilege(%s, p.oid, 'EXECUTE')",
            (list(SYSTEM), role)).fetchall()
        assert {name for name, _ in rows} == FUNCTIONS
        for _, config in rows:
            assert "search_path=pg_catalog, pg_temp" in config


def test_other_roles_lost_execute():
    with psycopg.connect(DSN or "") as c:
        for function in FUNCTIONS:
            row = c.execute("SELECT proacl::text[] FROM pg_proc WHERE oid = %s::regprocedure",
                            (function,)).fetchone()
            assert row and row[0], function
            assert not [a for a in row[0] if a.startswith("=")], f"PUBLIC can run {function}"
            for other in ("query_api", "ops_reader"):
                allowed = c.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                                    (other, function)).fetchone()
                assert allowed == (False,), (other, function)
