"""sql_guard: the adversarial suite at the parser (each case is refused before any database
work), then what passes and how it is rewritten. The database's own refusals, RLS and the limits
are in test_sql_workspace_db.py."""

import os

import pytest

from query_api.sql_guard import FUNCTIONS, MAX_SQL_CHARS, RELATIONS, Refused, check

REFUSED = {
    # the session: app.user_id, the role and every other setting
    "SET app.user_id": "SET app.user_id = 'bob'",
    "SET LOCAL": "SET LOCAL app.user_id TO 'bob'",
    "RESET": "RESET app.user_id",
    "RESET ROLE": "RESET ROLE",
    "SET ROLE": "SET ROLE query_api",
    "set_config": "SELECT set_config('app.user_id', 'bob', true)",
    "set_config role": "SELECT set_config('role', 'tributary', true)",
    "set_config in a subquery": "SELECT (SELECT set_config('app.user_id', 'bob', true))",
    "set_config qualified": "SELECT pg_catalog.set_config('app.user_id', 'bob', true)",
    "set_config quoted": 'SELECT "pg_catalog"."set_config"(\'app.user_id\', \'bob\', true)',
    "current_setting": "SELECT current_setting('app.user_id')",
    "current_setting in WHERE": "SELECT * FROM financials WHERE company_id ="
                                " current_setting('app.user_id')",
    "current_setting in VALUES": "VALUES (current_setting('app.user_id'))",
    "current_setting in ARRAY()": "SELECT ARRAY(SELECT current_setting('app.user_id'))",
    "current_user": "SELECT current_user",
    "session_user": "SELECT session_user",
    "current_schema": "SELECT current_schema",
    "system_user": "SELECT system_user",
    # sleeping, locking, generating
    "pg_sleep": "SELECT pg_sleep(30)",
    "pg_sleep_for": "SELECT pg_sleep_for('1 minute')",
    "advisory lock": "SELECT pg_advisory_lock(1)",
    "generate_series": "SELECT * FROM generate_series(1, 100000000)",
    "generate_series in SELECT": "SELECT generate_series(1, 100000000)",
    "repeat": "SELECT repeat('x', 1000000000)",
    "lpad": "SELECT lpad('x', 1000000000)",
    "string_agg (one unbounded value)": "SELECT string_agg(source_tag, ',') FROM financials",
    "array_agg (one unbounded value)": "SELECT array_agg(value) FROM financials",
    "recursive CTE": "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t)"
                     " SELECT count(*) FROM t",
    # catalogs and other schemas
    "pg_class": "SELECT * FROM pg_class",
    "pg_catalog.pg_authid": "SELECT * FROM pg_catalog.pg_authid",
    "pg_stat_activity": "SELECT query FROM pg_stat_activity",
    "information_schema": "SELECT * FROM information_schema.tables",
    "pg_class joined": "SELECT * FROM financials f JOIN pg_catalog.pg_roles r ON true",
    "pg_class in EXISTS": "SELECT 1 FROM financials WHERE EXISTS (SELECT 1 FROM pg_class)",
    "TABLE pg_class": "TABLE pg_class",
    "gold": "SELECT * FROM gold.quarterly_financials",
    "silver text": "SELECT text FROM silver.filing_sections",
    "bronze": "SELECT content_gzip FROM bronze.filing_documents",
    "userdata": "SELECT * FROM userdata.rows",
    "authz": "SELECT * FROM authz.user_access",
    "ops": "SELECT * FROM ops.quality_findings",
    "another database": "SELECT * FROM app.public.users",
    "a quoted, differently cased name": 'SELECT * FROM workspace."Financials"',
    "regclass": "SELECT 'pg_authid'::regclass",
    "regproc cast": "SELECT CAST('set_config' AS regproc)",
    "xid cast": "SELECT age('1'::xid)",
    # SECURITY DEFINER and other functions outside the list
    "definer": "SELECT * FROM gold.company_financials_at('0000320193', CURRENT_DATE, 'ttm',"
               " ARRAY['revenue'])",
    "search_page": "SELECT * FROM gold.search_page(NULL, NULL, NULL, NULL, NULL, NULL, 'date',"
                   " 1, 0, NULL)",
    "ops function": "SELECT * FROM ops.public_quality()",
    "dblink": "SELECT dblink('host=x', 'select 1')",
    "large object": "SELECT lo_import('/etc/passwd')",
    "pg_read_file": "SELECT pg_read_file('postgresql.conf')",
    "pg_ls_dir in FROM": "SELECT * FROM ROWS FROM (pg_ls_dir('.'))",
    "query_to_xml": "SELECT query_to_xml('select * from pg_authid', true, true, '')",
    "txid": "SELECT txid_current()",
    "notify": "SELECT pg_notify('x', 'y')",
    "nextval": "SELECT nextval('x')",
    "cancel": "SELECT pg_cancel_backend(1)",
    "unknown function": "SELECT my_function()",
    # writing
    "data-modifying CTE": "WITH d AS (DELETE FROM userdata.rows RETURNING *) SELECT * FROM d",
    "INSERT CTE": "WITH i AS (INSERT INTO x VALUES (1) RETURNING 1) SELECT * FROM i",
    "UPDATE": "UPDATE userdata.rows SET vals = '{1}'",
    "SELECT INTO": "SELECT * INTO stolen FROM financials",
    "FOR UPDATE": "SELECT * FROM financials FOR UPDATE",
    "FOR SHARE": "SELECT * FROM financials FOR KEY SHARE",
    "COPY": "COPY (SELECT 1) TO PROGRAM 'id'",
    "COPY TO file": "COPY financials TO '/tmp/x'",
    "CREATE": "CREATE TABLE x AS SELECT 1",
    "DO": "DO $$ BEGIN PERFORM 1; END $$",
    "CALL": "CALL x()",
    # statements, prepared statements, parameters
    "two statements": "SELECT 1; SELECT 2",
    "a second statement after a comment": "SELECT 1 -- x\n; DELETE FROM userdata.rows",
    "PREPARE": "PREPARE p AS SELECT 1",
    "EXECUTE": "EXECUTE p",
    "DEALLOCATE": "DEALLOCATE ALL",
    "DECLARE": "DECLARE c CURSOR FOR SELECT 1",
    "EXPLAIN ANALYZE": "EXPLAIN ANALYZE SELECT 1",
    "parameter": "SELECT $1",
    "psql meta-command": "SELECT 1 \\gexec",
    # comments and unicode
    "NUL hides a second statement": "SELECT 1 \x00; DELETE FROM userdata.rows",
    "unicode escape for pg_class": 'SELECT * FROM U&"\\0070g_class"',
    "unicode escape for set_config": 'SELECT U&"set\\005fconfig"(\'role\', \'x\', true)',
    "fullwidth letters": "SELECT * FROM ｐｇ_class",
    "zero-width space in a name": "SELECT * FROM fin​ancials",
    "comment inside a name": "SELECT * FROM pg_/**/class",
    "dollar-quoted statement": "SELECT $$x$$; SELECT set_config('role', 'x', true)",
    # a CTE named like a catalog, read outside its scope
    "CTE out of scope": "SELECT * FROM (WITH pg_class AS (SELECT 1) SELECT 1) a, pg_class",
    "CTE of a sibling subquery": "SELECT * FROM (WITH x AS (SELECT 1) SELECT 1) a,"
                                 " (SELECT * FROM pg_roles) b",
    # other constructs
    "TABLESAMPLE": "SELECT * FROM financials TABLESAMPLE SYSTEM (1)",
    "qualified operator": "SELECT 1 OPERATOR(pg_catalog.+) 1",
    "qualified ORDER BY USING": "SELECT x FROM financials ORDER BY x USING OPERATOR(pg_catalog.<)",
    "COLLATE": "SELECT 'a' COLLATE \"C\"",
    "XMLTABLE": "SELECT * FROM XMLTABLE('/a' PASSING '<a/>' COLUMNS x int)",
    "JSON constructor": "SELECT json_object('a': 1)",
    "column definition list": "SELECT * FROM financials_at('2020-01-01') AS x(a int)",
    "SETOF type": "SELECT CAST(1 AS SETOF int)",
    "empty": "  ",
    "too long": "SELECT 1" + " " * MAX_SQL_CHARS,
}


@pytest.mark.parametrize("query", REFUSED.values(), ids=REFUSED.keys())
def test_refused(query):
    with pytest.raises(Refused):
        check(query)


ALLOWED = [
    "SELECT 1",
    "select * from financials where company_id = '0000320193' and metric = 'revenue'",
    "SELECT * FROM workspace.financials",
    "SELECT f.value FROM financials_at(DATE '2023-12-31') f",
    "SELECT * FROM workspace.restatements('0000320193')",
    "SELECT r.* FROM companies c CROSS JOIN LATERAL reading(c.company_id, 'revenue') r",
    "WITH t AS (SELECT * FROM latest_figures), u AS (SELECT * FROM t) SELECT * FROM u",
    "WITH pg_class AS (SELECT 1 AS x) SELECT x FROM pg_class",  # the CTE, in scope
    "WITH financials AS (SELECT 1 AS x) SELECT x FROM financials",
    "SELECT * FROM (SELECT 1) a UNION ALL SELECT 2",
    "WITH x AS (SELECT 1 AS a) (SELECT a FROM x) UNION (SELECT a FROM x)",
    "SELECT extract(year FROM filed), date_trunc('month', filed), filed - interval '1 year'"
    " FROM financials",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value),"
    " count(*) FILTER (WHERE value > 0), count(DISTINCT unit) FROM financials",
    "SELECT company_id, value, lag(value, 4) OVER (PARTITION BY company_id ORDER BY period_end),"
    " rank() OVER w FROM financials WINDOW w AS (ORDER BY value DESC)",
    "SELECT name FROM companies WHERE name ILIKE '%apple%' OR sic SIMILAR TO '36%'",
    "SELECT CASE WHEN value > 0 THEN 'up' ELSE 'down' END, coalesce(value, 0),"
    " nullif(value, 0), greatest(1, 2), value IS DISTINCT FROM 0, value BETWEEN 1 AND 2"
    " FROM financials",
    "SELECT x FROM unnest(ARRAY[1, 2]) WITH ORDINALITY AS u(x, n)",
    "SELECT 1::numeric(10, 2), '2020-01-01'::date, 'x'::text, 1::double precision,"
    " '1 day'::interval, TIMESTAMP WITH TIME ZONE '2020-01-01 00:00Z', 1::int[]",
    "SELECT * FROM my_series s JOIN my_datasets d ON d.name = s.dataset",
    "SELECT company_id FROM companies WHERE 'AAPL' = ANY (tickers)",
    "SELECT * FROM financials ORDER BY value DESC NULLS LAST LIMIT 10 OFFSET 5",
    "SELECT * FROM financials ORDER BY value FETCH FIRST 3 ROWS WITH TIES",
    "SELECT company_id, sum(value) FROM financials GROUP BY ROLLUP (company_id)",
    "SELECT CURRENT_DATE, CURRENT_TIMESTAMP, LOCALTIMESTAMP, now()",
    "/* a comment */ SELECT 1 -- and another\n;",
    'SELECT U&"\\0063ompanies".name FROM U&"\\0063ompanies"',  # "companies"
    "SELECT $$text$$",
]


@pytest.mark.parametrize("query", ALLOWED)
def test_allowed(query):
    check(query)


def test_what_runs_is_the_checked_statement_without_comments():
    c = check("select /* x */ company_id -- y\nfrom workspace.financials f where f.value > 1;")
    assert c.sql == "SELECT company_id FROM workspace.financials AS f WHERE f.value > 1"
    assert c.relations == {"financials"} and c.functions == frozenset()
    c = check("SELECT * FROM reading('1', 'revenue') r, companies")
    assert c.functions == {"reading"} and c.relations == {"companies"}


def test_a_syntax_error_says_where():
    with pytest.raises(Refused) as e:
        check("SELECT FROM WHERE")
    assert e.value.position == 12 and "syntax error" in e.value.message


def test_a_refusal_names_what_was_refused_never_data():
    with pytest.raises(Refused) as e:
        check("SELECT * FROM gold.quarterly_financials WHERE value = 12345")
    assert "gold.quarterly_financials" in e.value.message and "12345" not in e.value.message


def test_deep_nesting_is_refused_not_a_crash():
    with pytest.raises(Refused):
        check("SELECT " + "+".join(["1"] * 9000))
    with pytest.raises(Refused):
        check("SELECT " + "(SELECT " * 1500 + "1" + ")" * 1500)


def test_the_lists_hold_no_function_with_side_effects():
    """Every allowlisted function is a pg_catalog built-in that is never volatile (set_config,
    pg_sleep, advisory locks, nextval and random all are)."""
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg
    with psycopg.connect(dsn) as conn:
        found = conn.execute(
            "SELECT proname, bool_or(provolatile = 'v'), bool_or(prosecdef)"
            " FROM pg_proc WHERE pronamespace = 'pg_catalog'::regnamespace"
            "   AND proname = ANY(%s) GROUP BY proname", (sorted(FUNCTIONS),)).fetchall()
        assert {name for name, _, _ in found} == FUNCTIONS
        assert [name for name, volatile, definer in found if volatile or definer] == []
        views = conn.execute("SELECT relname FROM pg_class WHERE relkind = 'v'"
                             " AND relnamespace = 'workspace'::regnamespace").fetchall()
        assert {v for (v,) in views} == RELATIONS
