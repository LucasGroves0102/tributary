"""The SQL workspace (B3a) against real Postgres: adversarial cases first (another user's rows,
companies outside the caller's grants with the catalog closed and open, a session that changes
under the query, what the database itself refuses when the guard is bypassed, quals that would
read hidden rows), then the limits (cost ceiling, row cap, timeout, one query per user, slots,
the daily budget) and the examples and schema browser.

Skipped unless TEST_DATABASE_URL is set; every test is rolled back. CI connects as a superuser
and migrates as a login like hermes' (workspace_reader created by the superuser first)."""

import os
from contextlib import contextmanager
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from query_api import sql_workspace
from query_api.app import create_app
from query_api.authorizer import Access
from query_api.sql_guard import Checked
from query_api.sql_workspace import (
    EXAMPLES,
    MAX_ROWS,
    SLOT_LOCK,
    SLOTS,
    USER_LOCK,
    Failed,
    Limits,
    Tampered,
)
from query_api.store import Store

from .conftest import SECRET
from .test_sql_workspace import FakeUsage

psycopg = pytest.importorskip("psycopg")

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

ANN, BEN, NOBODY = "t-sql-ann", "t-sql-ben", "t-sql-nobody"
A, B = "9100000001", "9100000002"  # tickers TSQA, TSQB
B_REVENUE = "777.77"  # B's only non-integer figure: an error quoting it would be a leak
NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class Fga:
    """OpenFGA reachable (layer 1 of /v1/sql only asks that); the rows are RLS's."""

    def can(self, *_):
        return True

    def access(self, user_id):
        return Access(frozenset(), {})


def _seed(conn):
    for cid, ticker, revenue in ((A, "TSQA", "100"), (B, "TSQB", B_REVENUE)):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tickers,"
                     " sic, fiscal_year_end) VALUES (%s, %s, %s, %s, '3674', '1231')",
                     (cid, f"Test {ticker}", cid, [ticker]))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
        for end, filed, value in (("2026-03-31", "2026-05-01", revenue),
                                  ("2026-06-30", "2026-08-01", revenue)):
            acc = f"{cid}-26-{end[5:7]}0000"
            conn.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_start,"
                " period_end, value, unit, value_source, source_tag, accession_no, form, filed,"
                " source_accessions, ingested_at) VALUES (%s, 'revenue', %s::date - 90, %s, %s,"
                " 'USD', 'reported', 'us-gaap:Revenues', %s, '10-Q', %s, ARRAY[%s], now())",
                (cid, end, end, value, acc, filed, acc))
        conn.execute(
            "INSERT INTO gold.derived_financials (company_id, span, metric, period_start,"
            " period_end, value, unit, value_source, inputs, accession_no, filed,"
            " source_accessions, ingested_at) VALUES (%s, 'ttm', 'gross_margin', '2025-07-01',"
            " '2026-06-30', 1, 'ratio', 'computed', '{}', %s, '2026-08-01', ARRAY[%s], now())",
            (cid, f"{cid}-26-060000", f"{cid}-26-060000"))
        # history: the March quarter first filed lower, restated in August
        conn.execute(
            "INSERT INTO gold.financials_known (company_id, span, metric, period_end, known_from,"
            " known_to, period_start, value, unit, value_source, source_tag, accession_no, form,"
            " filed, source_accessions, ingested_at) VALUES"
            " (%(c)s, 'quarter', 'revenue', '2026-03-31', '2026-05-01', '2026-08-01',"
            "  '2025-12-31', 90, 'USD', 'reported', 'us-gaap:Revenues', %(a1)s, '10-Q',"
            "  '2026-05-01', ARRAY[%(a1)s], now()),"
            " (%(c)s, 'quarter', 'revenue', '2026-03-31', '2026-08-01', NULL, '2025-12-31',"
            "  %(v)s, 'USD', 'reported', 'us-gaap:Revenues', %(a2)s, '10-Q', '2026-08-01',"
            "  ARRAY[%(a2)s], now())",
            {"c": cid, "v": revenue, "a1": f"{cid}-26-030000", "a2": f"{cid}-26-060000"})
        acc = f"{cid}-26-000002"
        conn.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
            " document_name, url, content_gzip, content_sha256, size_bytes)"
            " VALUES (%s, %s, '10-K', '2026-02-01', 'd.htm', 'u', '\\x00', 'x', 1)", (acc, cid))
        conn.execute(
            "INSERT INTO silver.filing_sections (accession_no, section_key, parser,"
            " parser_version, item, title, text, char_start, char_end)"
            " VALUES (%s, '1A', 'python', 't', '1A', 'Risk Factors', 'secret risks', 0, 12)",
            (acc,))
    conn.execute("SELECT gold.refresh_screen(%s)", ([A, B],))
    conn.execute("DELETE FROM authz.public_access")
    conn.execute("DELETE FROM authz.user_access WHERE user_id LIKE 't-sql-%%'")
    conn.execute("DELETE FROM authz.user_tiers WHERE user_id LIKE 't-sql-%%'")
    conn.execute("INSERT INTO authz.user_access (user_id, company_id, tier) VALUES"
                 " (%s, %s, 'financials'), (%s, %s, 'filings'), (%s, %s, 'financials')",
                 (ANN, A, ANN, A, BEN, B))
    conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials'),"
                 " (%s, 'filings'), (%s, 'financials')", (ANN, ANN, BEN))
    for user, name, cid in ((ANN, "stores", A), (BEN, "secret", B)):
        dataset = conn.execute(
            "INSERT INTO userdata.datasets (user_id, name, title, fields, units, known_from,"
            " row_count) VALUES (%s, %s, %s, '{count}', '{number}', 'upload', 1)"
            " RETURNING dataset_id", (user, name, name.title())).fetchone()[0]
        conn.execute("INSERT INTO userdata.rows (dataset_id, user_id, company_id, day,"
                     " known_at, vals) VALUES (%s, %s, %s, '2026-06-30', '2026-07-01', '{42}')",
                     (dataset, user, cid))


def open_catalog(conn):
    conn.execute("INSERT INTO authz.public_access (company_id, tier)"
                 " SELECT c, t FROM unnest(%s::text[]) c, unnest('{financials,filings}'::text[]) t",
                 ([A, B],))


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        _seed(conn)
        yield conn


def client(conn, usage=None, limits=None):
    return TestClient(create_app(Fga(), Store(OneConnection(conn)), SECRET,
                                 sql_usage=usage if usage is not None else FakeUsage(),
                                 sql_limits=limits or Limits(), clock=lambda: NOW))


def sql(c, token, user, query):
    return c.post("/v1/sql", json={"sql": query}, headers=token(user))


def rows(c, token, user, query):
    r = sql(c, token, user, query)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def ids(c, token, user, query):
    return sorted(r[0] for r in rows(c, token, user, query))


def raw(conn, user, query, limits=None):
    """Bypass the guard: what the database alone does with `query` as `user`."""
    return Store(OneConnection(conn)).run_sql(
        user, Checked(query, frozenset(), frozenset()), limits or Limits())


# --- adversarial: rows outside the caller's grants -------------------------------------------

READS = {
    "financials": "SELECT DISTINCT company_id FROM financials",
    "history": "SELECT DISTINCT company_id FROM financials_history",
    "latest": "SELECT DISTINCT company_id FROM latest_figures",
    "as of": "SELECT DISTINCT company_id FROM financials_at(DATE '2026-09-01')",
    "sections": "SELECT DISTINCT company_id FROM sections",
}


@pytest.mark.parametrize("what", sorted(READS))
def test_catalog_closed_reads_only_private_grants(db, token, what):
    c = client(db)
    if what == "sections":  # the filings tier: ann's grant on A; ben has none
        assert ids(c, token, ANN, READS[what]) == [A]
        assert ids(c, token, BEN, READS[what]) == []
    else:
        assert ids(c, token, ANN, READS[what]) == [A]
        assert ids(c, token, BEN, READS[what]) == [B]
    assert ids(c, token, NOBODY, READS[what]) == []


@pytest.mark.parametrize("what", sorted(READS))
def test_catalog_open_reads_the_catalog_within_each_users_tiers(db, token, what):
    open_catalog(db)
    c = client(db)
    assert ids(c, token, ANN, READS[what]) == [A, B]
    # ben's plan has no filings tier: the open catalog gives him financials only
    assert ids(c, token, BEN, READS[what]) == ([] if what == "sections" else [A, B])
    assert ids(c, token, NOBODY, READS[what]) == []  # no plan: no tier, nothing


def test_per_company_functions_read_nothing_outside_grants(db, token):
    c = client(db)
    assert rows(c, token, ANN, f"SELECT * FROM restatements('{B}')") == []
    got = rows(c, token, ANN, f"SELECT value, period_end, unit FROM reading('{B}', 'revenue',"
                              " 'quarter')")
    assert all(r == [None, None, None] for r in got)  # a blank, never B's figure
    restated = rows(c, token, ANN, f"SELECT original, restated FROM restatements('{A}')")
    assert restated == [["90", "100"]]
    assert rows(c, token, ANN, f"SELECT value FROM reading('{A}', 'revenue', 'quarter')") \
        == [["100"]]


def test_another_users_own_data_is_never_read(db, token):
    c = client(db)
    assert rows(c, token, ANN, "SELECT name FROM my_datasets") == [["stores"]]
    assert rows(c, token, BEN, "SELECT name FROM my_datasets") == [["secret"]]
    assert rows(c, token, ANN, "SELECT dataset, company_id, value FROM my_series") == [
        ["stores", A, "42"]]
    assert rows(c, token, ANN, "SELECT * FROM my_series WHERE dataset = 'secret'") == []
    assert rows(c, token, NOBODY, "SELECT count(*) FROM my_series") == [[0]]
    # with the guard bypassed, the tables themselves: RLS keeps them owner-only
    assert raw(db, ANN, "SELECT DISTINCT user_id FROM userdata.rows").rows == [[ANN]]
    assert raw(db, ANN, "SELECT DISTINCT user_id FROM userdata.datasets").rows == [[ANN]]


def test_a_qual_never_runs_on_rows_rls_hides(db, token):
    """B's 777.77 fails an integer cast: closed, the cast never sees it (no error, no value in
    a message); open, ann reads B and gets Postgres' error about her own row."""
    query = "SELECT company_id FROM financials WHERE value::text::int > 0"
    c = client(db)
    assert ids(c, token, ANN, query) == [A, A, A]
    open_catalog(db)
    r = sql(c, token, ANN, query)
    assert r.status_code == 422 and r.json()["code"] == "sql_error"
    assert "invalid input syntax" in r.json()["detail"]


# --- adversarial: the session and the role ------------------------------------------------------

@pytest.mark.parametrize("query", [
    f"SELECT set_config('app.user_id', '{BEN}', true)",
    "SELECT set_config('role', 'query_api', true)",
    "SELECT set_config('app.user_id', '', true), set_config('app.user_id', current_user, true)",
])
def test_a_session_changed_under_the_query_returns_nothing(db, query):
    """If the guard were bypassed, a query changing app.user_id or the role is caught before
    any row is returned."""
    with pytest.raises(Tampered):
        raw(db, ANN, query)


@pytest.mark.parametrize("query", [
    "SELECT * FROM gold.company_financials_at('9100000002', CURRENT_DATE, 'ttm', '{revenue}')",
    "SELECT * FROM gold.search_page(NULL, NULL, NULL, NULL, NULL, NULL, 'date', 1, 0, NULL)",
    "SELECT * FROM gold.disagreeing_figures('{9100000002}')",
    "SELECT count(*) FROM bronze.filings",
    "SELECT content_gzip FROM bronze.filing_documents",
    "SELECT * FROM ops.public_quality()",
    "SELECT count(*) FROM ops.quality_findings",
    "SELECT * FROM silver.financial_facts",
    "SELECT set_config('temp_file_limit', '10GB', true)",
    "WITH d AS (DELETE FROM userdata.rows RETURNING 1) SELECT count(*) FROM d",
    "SELECT pg_read_file('postgresql.conf')",
    "SELECT * FROM pg_authid",
])
def test_the_database_refuses_what_the_guard_refuses(db, query):
    """Second line: workspace_reader in a read-only transaction can't reach DEFINER functions,
    bronze/ops/silver facts, other settings or writes even if the guard let a query through."""
    with pytest.raises(Failed) as e:
        raw(db, ANN, query)
    assert e.value.code == "sql_error"


def test_no_security_definer_function_is_executable_by_workspace_reader(db):
    found = db.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE p.prosecdef AND has_function_privilege('workspace_reader', p.oid, 'EXECUTE')"
    ).fetchall()
    assert found == []


def test_workspace_reader_is_a_bare_role(db):
    role = db.execute("SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb,"
                      " rolinherit FROM pg_roles WHERE rolname = 'workspace_reader'").fetchone()
    assert role[:5] == (False, False, False, False, False)
    assert db.execute("SELECT count(*) FROM pg_auth_members WHERE member ="
                      " 'workspace_reader'::regrole").fetchone()[0] == 0
    # query_api may SET ROLE to it, gains none of its rights or policies
    assert db.execute("SELECT pg_has_role('query_api', 'workspace_reader', 'SET'),"
                      " pg_has_role('query_api', 'workspace_reader', 'USAGE')").fetchone() \
        == (True, False)
    # writes nothing anywhere, creates nothing
    writable = db.execute(
        "SELECT c.oid::regclass::text FROM pg_class c"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')"
        "   AND c.relkind IN ('r', 'v', 'p')"
        "   AND (has_table_privilege('workspace_reader', c.oid, 'INSERT')"
        "        OR has_table_privilege('workspace_reader', c.oid, 'UPDATE')"
        "        OR has_table_privilege('workspace_reader', c.oid, 'DELETE'))").fetchall()
    assert writable == []
    schemas = db.execute("SELECT nspname FROM pg_namespace WHERE has_schema_privilege("
                         "'workspace_reader', oid, 'CREATE')").fetchall()
    assert schemas == []


def test_policies_name_the_same_rule_for_both_roles(db):
    """105 ALTERed each policy's roles in place: one expression, query_api's and
    workspace_reader's alike."""
    rows_ = db.execute(
        "SELECT schemaname || '.' || tablename, roles::text[] FROM pg_policies"
        " WHERE 'workspace_reader' = ANY (roles)").fetchall()
    assert {t for t, _ in rows_} == {
        "gold.quarterly_financials", "gold.derived_financials", "gold.financials_known",
        "gold.screen_figures", "silver.filing_sections", "authz.user_access",
        "authz.user_tiers", "authz.public_access", "userdata.datasets", "userdata.rows"}
    assert all(sorted(r) == ["query_api", "workspace_reader"] for _, r in rows_)


def test_the_callers_transaction_is_left_as_it_was(db, token):
    c = client(db)
    rows(c, token, ANN, "SELECT 1")
    assert db.execute("SELECT current_user = session_user,"
                      " current_setting('transaction_read_only'),"
                      " current_setting('app.user_id', true)").fetchone() in (
        (True, "off", ""), (True, "off", None))
    db.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES ('t-sql-x', 'filings')")


def test_comments_and_a_second_statement_hidden_in_one_never_run(db, token):
    c = client(db)
    got = rows(c, token, ANN, "/* x; */ SELECT DISTINCT company_id FROM financials -- ; SELECT *"
                              " FROM my_series\n WHERE period_end = '2026-06-30'")
    assert got == [[A]]


# --- limits --------------------------------------------------------------------------------------

def test_a_cartesian_join_is_refused_before_it_runs(db, token):
    """Rows ceiling: a join without a condition expects a cross product at one step."""
    usage = FakeUsage()
    c = client(db, usage)
    r = sql(c, token, ANN, "SELECT count(*) FROM metrics a, metrics b, metrics c")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "too_costly" and r.json()["rows"] > Limits().max_plan_rows
    assert "a join without a condition" in r.json()["detail"]
    assert usage.seconds == {}


def test_an_absurd_plan_is_refused_on_its_cost(db, token):
    """Cost ceiling: a correlated subquery over a view for every row of it (few rows expected
    at any step, much work; lowered to this fixture's size)."""
    r = sql(client(db, limits=Limits(max_cost=1e4)), token, ANN,
            "SELECT (SELECT count(*) FROM metrics b WHERE b.metric > a.metric) FROM metrics a")
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "too_costly" and body["cost"] > 1e4 and body["rows"] < 1e9
    assert "estimated cost" in body["detail"]


def test_a_huge_result_stops_at_the_row_cap(db, token):
    numbers = ", ".join(str(i) for i in range(120))  # 120 x 120 = 14,400 rows
    r = sql(client(db), token, ANN, f"SELECT a, b FROM unnest(ARRAY[{numbers}]) a,"
                                    f" unnest(ARRAY[{numbers}]) b")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row_count"] == MAX_ROWS and body["truncated"] == "rows"
    assert [c["name"] for c in body["columns"]] == ["a", "b"]


def test_a_slow_query_stops_at_the_timeout_and_counts(db, token):
    usage = FakeUsage()
    c = client(db, usage, Limits(statement_ms=200, max_cost=1e15, max_plan_rows=1e15))
    r = sql(c, token, ANN, "SELECT count(*) FROM metrics a, metrics b, metrics c, metrics d")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "timeout"
    assert usage.seconds[(ANN, NOW.date())] > 0.1


def test_one_running_query_per_user(db, token):
    c = client(db)
    with psycopg.connect(DSN, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", (USER_LOCK, ANN))
        try:
            r = sql(c, token, ANN, "SELECT 1")
            assert r.status_code == 429 and r.json()["code"] == "busy_user"
            assert sql(c, token, BEN, "SELECT 1").status_code == 200  # others still run
        finally:
            other.execute("SELECT pg_advisory_unlock_all()")
    assert sql(c, token, ANN, "SELECT 1").status_code == 200


def test_at_most_four_at_once_across_replicas(db, token):
    c = client(db)
    with psycopg.connect(DSN, autocommit=True) as other:
        for i in range(1, SLOTS + 1):
            other.execute("SELECT pg_advisory_lock(%s, %s)", (SLOT_LOCK, i))
        try:
            r = sql(c, token, ANN, "SELECT 1")
            assert r.status_code == 429 and r.json()["code"] == "busy_slots"
            other.execute("SELECT pg_advisory_unlock(%s, %s)", (SLOT_LOCK, 3))
            assert sql(c, token, ANN, "SELECT 1").status_code == 200
        finally:
            other.execute("SELECT pg_advisory_unlock_all()")


def test_the_daily_budget(db, token):
    usage = FakeUsage({(ANN, NOW.date()): 600.0})
    c = client(db, usage)
    r = sql(c, token, ANN, "SELECT 1")
    assert r.status_code == 429 and r.json()["code"] == "budget"
    r = sql(c, token, BEN, "SELECT 1")
    assert r.status_code == 200
    assert r.json()["budget"]["limit_seconds"] == 600
    assert usage.seconds[(BEN, NOW.date())] > 0


def test_values_types_and_the_statement_as_run(db, token):
    r = sql(client(db), token, ANN,
            "SELECT company_id, period_end, value, value > 0 AS up, 1 AS n, 2.5::float8 AS f,"
            " filed - period_end AS lag, interval '1 day' AS i FROM financials"
            " WHERE span = 'quarter' ORDER BY period_end LIMIT 1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(c["name"], c["type"]) for c in body["columns"]] == [
        ("company_id", "text"), ("period_end", "date"), ("value", "number"),
        ("up", "boolean"), ("n", "number"), ("f", "number"), ("lag", "number"),
        ("i", "interval")]
    assert body["rows"] == [[A, "2026-03-31", "100", True, 1, 2.5, 31, "1 day"]]
    assert body["sql"].startswith("SELECT company_id") and body["reads"] == ["financials"]


# --- the examples and the schema browser -------------------------------------------------------

@pytest.mark.parametrize("example", EXAMPLES, ids=[e["title"] for e in EXAMPLES])
def test_every_example_runs(db, token, example):
    open_catalog(db)
    db.execute("UPDATE silver.companies SET tickers = '{AAPL}' WHERE company_id = %s", (A,))
    r = sql(client(db), token, ANN, example["sql"])
    assert r.status_code == 200, r.text


def test_schema_lists_every_view_and_function_described(db, token):
    r = client(db).get("/v1/sql/schema", headers=token(ANN))
    assert r.status_code == 200, r.text
    objects = {o["name"]: o for o in r.json()["objects"]}
    from query_api.sql_guard import RELATIONS, WORKSPACE_FUNCTIONS
    assert set(objects) == RELATIONS | WORKSPACE_FUNCTIONS
    for o in objects.values():
        assert o["description"], o["name"]
        assert o["columns"], o["name"]
        if o["kind"] == "view":
            assert all(c["description"] for c in o["columns"]), o["name"]
    assert objects["reading"]["arguments"].startswith("company_id text, metric text")
    assert r.json()["limits"]["rows"] == MAX_ROWS


def test_usage_is_owner_only_in_the_app_database():
    app_dsn = os.environ.get("APP_TEST_DATABASE_URL")
    if not app_dsn:
        pytest.skip("APP_TEST_DATABASE_URL not set")
    with psycopg.connect(app_dsn) as conn, conn.transaction(force_rollback=True):
        owner = conn.execute("SELECT tableowner FROM pg_tables WHERE tablename = 'sql_usage'"
                             ).fetchone()[0]
        conn.execute(f'SET LOCAL ROLE "{owner}"')
        conn.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@x.test'),"
                     " (%s, 'b@x.test')", (ANN, BEN))
        usage = sql_workspace.AppSqlUsage(OneConnection(conn))
        day = date(2026, 11, 17)
        usage.add(ANN, day, 1.25)
        usage.add(ANN, day, 2.0)
        usage.add(BEN, day, 5.0)
        assert usage.used(ANN, day) == 3.25 and usage.used(BEN, day) == 5.0
        assert usage.used(ANN, date(2026, 11, 18)) == 0.0
        # forced RLS: as ann, ben's row doesn't exist
        conn.execute("SELECT set_config('app.user_id', %s, true)", (ANN,))
        assert conn.execute("SELECT count(*) FROM sql_usage").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO sql_usage (user_id, day) VALUES (%s, %s)", (BEN, day))
