"""The SQL workspace (B3a): POST /v1/sql runs one checked SELECT over the `workspace` views
(migration 105) as the caller, and GET /v1/sql/schema lists those views with their columns'
descriptions and example queries. Notebooks (a workspace item kind, app migration 023) run their
cells through it one at a time; nothing here stores a result.

Who reads what: sql_guard.check() first (one SELECT naming only workspace views, allowlisted
functions and casts; what runs is the checked statement, deparsed). Then one READ ONLY
transaction (Store.run_sql) as query_api with app.user_id set by the server, the limits below set
locally, then `SET LOCAL ROLE workspace_reader`: row-level security exactly as every other route
(the financials and filings tiers, the catalog open or closed, private grants; userdata only the
caller's rows). Layer 1 is `access_of` (OpenFGA reachable for this user, else 503): a query names
no company in advance, so the rows are RLS's alone, like the screener with the catalog public.
After the rows are fetched the session must still be workspace_reader with the same app.user_id in
a read-only transaction, or nothing is returned (500, logged): a second line behind the guard.

Limits, all per query unless said: statement_timeout 15 s, lock_timeout 1 s, work_mem 32 MB,
temp_file_limit 256 MB (query_api holds SET on it: migration 105), no parallel workers, no JIT; the
plan (EXPLAIN before running) refused when its total cost is over SQL_MAX_COST or any step expects
more than SQL_MAX_PLAN_ROWS rows (a cartesian join; DEFAULT_MAX_COST says why both); at most MAX_ROWS rows
(fetched through a cursor, MAX_RESULT_BYTES of them at most); one running query per user and SLOTS
at once across replicas (transaction-scoped advisory locks: they go with the transaction); a daily
budget of query seconds per user (SQL_SECONDS_PER_DAY, counted in the app database: sql_usage,
app migration 023, owner-only). Errors carry Postgres' primary message and hint (names, and values
only from rows RLS already let through), never its detail; logs carry the SQLSTATE only.

API keys can't run SQL (neither route is in api_keys.KEY_ROUTES: default deny)."""

import json
import logging
import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from decimal import Decimal
from typing import Annotated, Any, Protocol

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from query_api import sql_guard
from query_api.sql_guard import Checked, Refused

log = logging.getLogger("query_api.sql")

STATEMENT_SECONDS = 15
LOCK_TIMEOUT = "1s"
WORK_MEM = "32MB"
TEMP_FILE_LIMIT = "256MB"
MAX_ROWS = 10_000
MAX_RESULT_BYTES = 8_000_000  # the rows as JSON, about: a wide result stops earlier
MAX_COLUMNS = 200
FETCH_BATCH = 1_000
# The plan ceilings, measured (services/query-api DESIGN.md "SQL workspace"). On hermes (read only,
# no RLS): one company's figures cost ~300; one metric across every company ~1.0M (6.4 s warm);
# count(*) over financials 2.2M; financials joined to itself 4.8e9. On a catalog-sized local copy as
# workspace_reader: reading() for 20 companies 6.2e9 yet 0.28 s (each LATERAL probe is charged the
# RLS policy's hashed subplan), a correlated subquery per row 2.6e11; the cartesian joins expect
# 1.4e10 to 1.9e11 rows at one step, every legitimate query under 4e6. So cost alone can't tell a
# slow query from an inflated estimate: the cost ceiling stops only the absurd, the rows ceiling
# stops cross products, and the timeout, the slots and the daily budget bound the rest.
DEFAULT_MAX_COST = 1e10
DEFAULT_MAX_PLAN_ROWS = 1e9
DEFAULT_SECONDS_PER_DAY = 600
SLOTS = 4
# advisory lock classes (pg_try_advisory_xact_lock(class, key)): no other lock in the database
# uses the two-int form with these
USER_LOCK = 0x5351_0001   # key: hashtext(user_id)
SLOT_LOCK = 0x5351_0002   # key: 1..SLOTS

# What a column holds, for the web (right-aligned numbers, charts, CSV): by type OID.
_KINDS = {16: "boolean", 20: "number", 21: "number", 23: "number", 26: "number", 700: "number",
          701: "number", 1700: "number", 25: "text", 1042: "text", 1043: "text", 19: "text",
          18: "text", 1082: "date", 1114: "timestamp", 1184: "timestamp", 1083: "time",
          1266: "time", 1186: "interval"}
# user-facing errors by SQLSTATE class: the query's own problem, said in Postgres' words
_USER_CLASSES = ("21", "22", "2F", "42", "0A", "54", "53", "55", "57", "25", "P0")


@dataclass(frozen=True)
class Limits:
    max_cost: float = DEFAULT_MAX_COST
    max_plan_rows: float = DEFAULT_MAX_PLAN_ROWS
    seconds_per_day: int = DEFAULT_SECONDS_PER_DAY
    slots: int = SLOTS
    statement_ms: int = STATEMENT_SECONDS * 1000  # statement_timeout (tests shorten it)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Limits":
        cost = float(env.get("SQL_MAX_COST") or DEFAULT_MAX_COST)
        rows = float(env.get("SQL_MAX_PLAN_ROWS") or DEFAULT_MAX_PLAN_ROWS)
        seconds = int(env.get("SQL_SECONDS_PER_DAY") or DEFAULT_SECONDS_PER_DAY)
        if not (math.isfinite(cost) and cost > 0 and math.isfinite(rows) and rows > 0
                and seconds > 0):
            raise ValueError("SQL_MAX_COST, SQL_MAX_PLAN_ROWS and SQL_SECONDS_PER_DAY must be"
                             " positive")
        return cls(max_cost=cost, max_plan_rows=rows, seconds_per_day=seconds)


class Busy(Exception):
    """`user`: this user already has a query running; `slots`: SLOTS queries are running."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class TooCostly(Exception):
    """The plan's estimated total cost, or the most rows any step of it expects, is over its
    ceiling: refused before it runs."""

    def __init__(self, cost: float, rows: float, limits: "Limits") -> None:
        super().__init__(f"cost {cost}, rows {rows}")
        self.cost, self.rows, self.limits = cost, rows, limits


class Failed(Exception):
    """The query failed in Postgres for its own reasons: `message` is safe to show."""

    def __init__(self, code: str, message: str, sqlstate: str | None, seconds: float) -> None:
        super().__init__(message)
        self.code, self.message, self.sqlstate, self.seconds = code, message, sqlstate, seconds


class Tampered(Exception):
    """The session changed under the query (role, user or read-only): never answer."""


@dataclass
class Result:
    columns: list[dict]
    rows: list[list[Any]]
    truncated: str | None  # None, "rows" (over MAX_ROWS) or "size" (over MAX_RESULT_BYTES)
    cost: float
    seconds: float
    relations: list[str] = field(default_factory=list)


def cell(value: Any) -> Any:
    """A value as JSON: numerics as exact decimal text (CSV keeps every digit; the web reads it
    as a number), big integers as text, dates and times ISO, anything else as text."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) < 2 ** 53 else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else str(value)
    if isinstance(value, (datetime, date, clock_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return interval_text(value)
    if isinstance(value, (list, tuple)):
        return [cell(v) for v in value]
    return str(value)


def interval_text(value: timedelta) -> str:
    days, rest = value.days, value.seconds
    out = f"{days} day{'s' if abs(days) != 1 else ''}" if days else ""
    if rest or value.microseconds or not out:
        h, rem = divmod(rest, 3600)
        m, s = divmod(rem, 60)
        stamp = f"{h:02d}:{m:02d}:{s:02d}" + (f".{value.microseconds:06d}"
                                               if value.microseconds else "")
        out = f"{out} {stamp}".strip()
    return out


def user_error(e: psycopg.Error, seconds: float, limits: Limits) -> Failed | None:
    """Failed for an error that is the query's own (shown in Postgres' words: its primary
    message and hint, never DETAIL); None for anything else (the caller re-raises)."""
    state = getattr(e, "sqlstate", None)
    if not state or state[:2] not in _USER_CLASSES:
        return None
    if isinstance(e, psycopg.errors.QueryCanceled):
        return Failed("timeout", f"the query ran longer than {limits.statement_ms / 1000:g}"
                                 " seconds: narrow it (fewer companies, metrics or periods) or"
                                 " read latest_figures", state, seconds)
    if state == "53400":  # configuration_limit_exceeded: temp_file_limit
        return Failed("too_large", f"the query needed more than {TEMP_FILE_LIMIT} of temporary"
                                   " space: narrow it or aggregate earlier", state, seconds)
    if state == "55P03":
        return Failed("busy", "a table was locked by Tributary's own jobs: try again in a moment",
                      state, seconds)
    diag = e.diag
    message = diag.message_primary or "the query failed"
    if diag.message_hint:
        message = f"{message} ({diag.message_hint})"
    return Failed("sql_error", message, state, seconds)


def run(pool: Any, user_id: str, checked: Checked, limits: Limits,
        clock: Callable[[], float] = time.monotonic) -> Result:
    """Store.run_sql's body: `checked` as `user_id` within the limits (module docstring)."""
    started = clock()
    with pool.connection() as conn, conn.transaction(force_rollback=True), \
            conn.cursor() as cur:
        # first statement of the transaction (a savepoint in tests: read-only applies too)
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute(
            "SELECT set_config('statement_timeout', %(timeout)s, true),"
            " set_config('lock_timeout', %(lock)s, true),"
            " set_config('idle_in_transaction_session_timeout', '60s', true),"
            " set_config('work_mem', %(work_mem)s, true),"
            " set_config('temp_file_limit', %(temp)s, true),"
            " set_config('max_parallel_workers_per_gather', '0', true),"
            " set_config('jit', 'off', true),"
            " set_config('search_path', 'workspace', true),"
            " set_config('app.user_id', %(user)s, true)",
            {"timeout": f"{limits.statement_ms}ms", "lock": LOCK_TIMEOUT, "work_mem": WORK_MEM,
             "temp": TEMP_FILE_LIMIT, "user": user_id})
        cur.execute("SELECT pg_try_advisory_xact_lock(%s, hashtext(%s))", (USER_LOCK, user_id))
        if not _fetch_one(cur):
            raise Busy("user")
        slot = " ".join(f"WHEN pg_try_advisory_xact_lock({SLOT_LOCK}, {i}) THEN {i}"
                        for i in range(1, limits.slots + 1))
        cur.execute(f"SELECT CASE {slot} ELSE 0 END")  # noqa: S608 - fixed text
        if not _fetch_one(cur):
            raise Busy("slots")
        cur.execute("SET LOCAL ROLE workspace_reader")
        try:
            # bytes: no placeholder processing (no parameters), never prepared
            cur.execute(f"EXPLAIN (FORMAT JSON) {checked.sql}".encode(), prepare=False)
            plan = _plan(_fetch_one(cur))["Plan"]
            cost = float(plan["Total Cost"])
            rows = max(float(n.get("Plan Rows", 0)) for n in _nodes(plan))
            if cost > limits.max_cost or rows > limits.max_plan_rows:
                raise TooCostly(cost, rows, limits)
            cur.execute(f"DECLARE workspace_query NO SCROLL CURSOR FOR {checked.sql}".encode(),
                        prepare=False)
            columns, rows, truncated = _fetch(cur)
        except psycopg.Error as e:
            failed = user_error(e, clock() - started, limits)
            if failed is None:
                raise
            raise failed from None
        cur.execute("SELECT current_user = 'workspace_reader'"
                    " AND current_setting('app.user_id', true) = %s"
                    " AND current_setting('transaction_read_only') = 'on'", (user_id,))
        if _fetch_one(cur) is not True:
            raise Tampered()
        return Result(columns, rows, truncated, cost, clock() - started,
                      sorted(checked.relations | checked.functions))


def _fetch_one(cur: psycopg.Cursor) -> Any:
    row = cur.fetchone()
    return None if row is None else row[0]


def _plan(value: Any) -> dict:
    data = json.loads(value) if isinstance(value, str) else value
    return data[0]


def _nodes(node: dict) -> Iterator[dict]:
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def _fetch(cur: psycopg.Cursor) -> tuple[list[dict], list[list[Any]], str | None]:
    rows: list[list[Any]] = []
    size, columns, truncated = 0, None, None
    while truncated is None:
        cur.execute(f"FETCH FORWARD {FETCH_BATCH} FROM workspace_query", prepare=False)
        if columns is None:
            described = cur.description or []
            if len(described) > MAX_COLUMNS:
                raise Failed("too_wide", f"the query returns {len(described)} columns: at most"
                                         f" {MAX_COLUMNS}", None, 0.0)
            columns = [{"name": d.name, "type": _KINDS.get(d.type_code, "text")}
                       for d in described]
        batch = cur.fetchall()
        for row in batch:
            if len(rows) == MAX_ROWS:
                truncated = "rows"
                break
            values = [cell(v) for v in row]
            size += len(json.dumps(values, default=str)) + 1
            if size > MAX_RESULT_BYTES:
                truncated = "size"
                break
            rows.append(values)
        if len(batch) < FETCH_BATCH and truncated is None:
            break
    return columns or [], rows, truncated


# --- the daily budget (app database) ----------------------------------------------------------

class SqlUsage(Protocol):
    def used(self, user_id: str, day: date) -> float: ...
    def add(self, user_id: str, day: date, seconds: float) -> None: ...


class AppSqlUsage:
    """Query seconds per user per UTC day (app migration 023): forced row-level security on
    app.user_id and a user_id filter on every statement."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            try:
                yield cur
            finally:
                if conn.info.transaction_status != TransactionStatus.INERROR:
                    cur.execute("SELECT set_config('app.user_id', '', true)")

    def used(self, user_id: str, day: date) -> float:
        with self._tx(user_id) as cur:
            cur.execute("SELECT seconds FROM sql_usage WHERE user_id = %s AND day = %s",
                        (user_id, day))
            row = cur.fetchone()
            return float(row["seconds"]) if row else 0.0

    def add(self, user_id: str, day: date, seconds: float) -> None:
        with self._tx(user_id) as cur:
            cur.execute(
                "INSERT INTO sql_usage AS u (user_id, day, seconds, queries)"
                " VALUES (%(user)s, %(day)s, %(seconds)s, 1)"
                " ON CONFLICT (user_id, day) DO UPDATE"
                " SET seconds = u.seconds + EXCLUDED.seconds, queries = u.queries + 1",
                {"user": user_id, "day": day, "seconds": round(max(seconds, 0.0), 3)})


# --- examples and the schema browser -----------------------------------------------------------

# Each runs as written (test_sql_workspace_db runs every one); the web offers them in the
# notebook. Amounts are compared only within one currency (unit). A company list reaches
# financials as `company_id = ANY (ARRAY(...))` or `= (SELECT ...)`: a value the planner pushes
# into the view (an index read), where a join would compute the view for every company first.
EXAMPLES: list[dict[str, str]] = [
    {"title": "Gross margin against a year earlier, the screener's top 20",
     "sql": """-- The 20 largest US-dollar reporters by trailing revenue (the screener's default
-- order), with gross margin now and four quarters earlier.
WITH top AS (
  SELECT company_id, value AS revenue_ttm
  FROM latest_figures
  WHERE span = 'ttm' AND metric = 'revenue' AND unit = 'USD'
  ORDER BY value DESC
  LIMIT 20
), margins AS (
  SELECT f.company_id, f.period_end, f.value,
         lag(f.value, 4) OVER (PARTITION BY f.company_id ORDER BY f.period_end) AS year_earlier,
         row_number() OVER (PARTITION BY f.company_id ORDER BY f.period_end DESC) AS newest
  FROM financials f
  WHERE f.company_id = ANY (ARRAY(SELECT company_id FROM top))
    AND f.span = 'ttm' AND f.metric = 'gross_margin'
)
SELECT c.name, m.period_end, round(m.value * 100, 1) AS gross_margin_pct,
       round(m.year_earlier * 100, 1) AS year_earlier_pct,
       round((m.value - m.year_earlier) * 100, 1) AS change_pts
FROM margins m
JOIN top t USING (company_id)
JOIN companies c USING (company_id)
WHERE m.newest = 1
ORDER BY t.revenue_ttm DESC"""},
    {"title": "A company's restatements",
     "sql": """-- Every figure Apple restated: the value first filed and the one that replaced it.
SELECT r.metric, r.span, r.period_end, r.original, r.restated,
       r.original_form, r.original_filed, r.restated_form, r.restated_filed, r.same_line
FROM companies c
CROSS JOIN LATERAL restatements(c.company_id) r
WHERE 'AAPL' = ANY (c.tickers)
ORDER BY r.restated_filed DESC, r.metric"""},
    {"title": "My dataset joined to revenue",
     "sql": """-- Each value of your first dataset beside the company's quarterly revenue for the
-- quarter that day falls in. Upload a CSV under Your data first.
WITH mine AS (
  SELECT s.* FROM my_series s
  WHERE s.dataset = (SELECT name FROM my_datasets ORDER BY uploaded_at LIMIT 1)
), revenue AS (
  SELECT company_id, period_start, period_end, value, unit
  FROM financials
  WHERE company_id = ANY (ARRAY(SELECT DISTINCT company_id FROM mine))
    AND span = 'quarter' AND metric = 'revenue'
)
SELECT c.name, m.day, m.field, m.value, r.period_end, r.value AS revenue, r.unit
FROM mine m
JOIN companies c USING (company_id)
LEFT JOIN revenue r
  ON r.company_id = m.company_id AND m.day BETWEEN r.period_start AND r.period_end
ORDER BY c.name, m.day, m.field"""},
    {"title": "Revenue as it was known on a past day",
     "sql": """-- Microsoft's quarterly revenue as the filings stood at the end of 2023: the version of
-- each quarter believed then, nothing filed later (no look-ahead).
SELECT f.period_end, f.value, f.unit, f.form, f.filed, f.known_from
FROM financials_at(DATE '2023-12-31') f
WHERE f.company_id = (SELECT company_id FROM companies WHERE 'MSFT' = ANY (tickers) LIMIT 1)
  AND f.span = 'quarter' AND f.metric = 'revenue'
ORDER BY f.period_end DESC
LIMIT 12"""},
    {"title": "Average gross margin by sector",
     "sql": """-- Median and average trailing gross margin per sector, latest figures.
SELECT c.sector, count(*) AS companies,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l.value)::numeric * 100, 1)
         AS median_pct,
       round(avg(l.value) * 100, 1) AS average_pct
FROM latest_figures l
JOIN companies c USING (company_id)
WHERE l.span = 'ttm' AND l.metric = 'gross_margin' AND l.value BETWEEN -1 AND 1
  AND c.sector IS NOT NULL
GROUP BY c.sector
ORDER BY median_pct DESC"""},
    {"title": "8-Ks with results (item 2.02) this year",
     "sql": """-- Parsed earnings releases filed this year, per company.
SELECT c.name, count(*) AS releases, max(s.filed) AS latest
FROM sections s
JOIN companies c USING (company_id)
WHERE s.form = '8-K' AND s.section_key = '2.02'
  AND s.filed >= date_trunc('year', CURRENT_DATE)
GROUP BY c.name
ORDER BY releases DESC, latest DESC
LIMIT 50"""},
]

SCHEMA_SQL = """
SELECT c.relname AS name, 'view' AS kind, obj_description(c.oid, 'pg_class') AS description,
       (SELECT json_agg(json_build_object(
                  'name', a.attname, 'type', format_type(a.atttypid, a.atttypmod),
                  'description', col_description(c.oid, a.attnum)) ORDER BY a.attnum)
        FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped)
           AS columns,
       NULL AS arguments
FROM pg_class c
WHERE c.relnamespace = 'workspace'::regnamespace AND c.relkind = 'v'
UNION ALL
SELECT p.proname, 'function', obj_description(p.oid, 'pg_proc'),
       (SELECT json_agg(json_build_object('name', o.name, 'type', format_type(o.type, NULL),
                                          'description', NULL) ORDER BY o.n)
        FROM unnest(p.proallargtypes, p.proargmodes, p.proargnames)
             WITH ORDINALITY o(type, mode, name, n)
        WHERE o.mode = 't'),
       pg_get_function_arguments(p.oid)
FROM pg_proc p
WHERE p.pronamespace = 'workspace'::regnamespace
ORDER BY 2 DESC, 1
"""


def schema_rows(cur: psycopg.Cursor) -> list[dict]:
    cur.execute(SCHEMA_SQL)
    out = []
    for r in cur.fetchall():
        row = dict(r) if not isinstance(r, dict) else r
        out.append({"name": row["name"], "kind": row["kind"],
                    "description": row["description"],
                    "arguments": row["arguments"],
                    "columns": row["columns"] or []})
    return out


# --- routes -----------------------------------------------------------------------------------

class SqlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=4 * sql_guard.MAX_SQL_CHARS)


SCHEMA_TTL_SECONDS = 600.0


def add_sql_routes(app: FastAPI, *, store: Any, usage: SqlUsage | None,
                   current_user: Callable[..., str], access_of: Callable[[str], Any],
                   limits: Limits, utcnow: Callable[[], datetime],
                   monotonic: Callable[[], float] = time.monotonic) -> None:
    User = Annotated[str, Depends(current_user)]
    cache: dict[str, Any] = {"until": float("-inf"), "items": None}

    def refuse(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({"detail": message, "code": code, **extra}, status_code=status)

    def budget(used: float) -> dict:
        return {"used_seconds": round(used, 1), "limit_seconds": limits.seconds_per_day}

    @app.get("/v1/sql/schema", response_model=dict)
    def sql_schema(user_id: User) -> dict:
        """The workspace's views and functions, each column described, the example queries
        and the limits. Names and descriptions only: no data."""
        now = monotonic()
        if cache["until"] <= now:
            cache["items"] = store.sql_schema()
            cache["until"] = now + SCHEMA_TTL_SECONDS
        return {"objects": cache["items"], "examples": EXAMPLES,
                "limits": {"rows": MAX_ROWS, "seconds": limits.statement_ms / 1000,
                           "max_cost": limits.max_cost, "max_plan_rows": limits.max_plan_rows,
                           "seconds_per_day": limits.seconds_per_day,
                           "sql_chars": sql_guard.MAX_SQL_CHARS}}

    @app.post("/v1/sql", response_model=dict)
    def run_sql(user_id: User, body: SqlRequest) -> Any:
        """One SELECT over the workspace views, as the caller (module docstring)."""
        if usage is None:
            raise HTTPException(503, "the SQL workspace is unavailable")
        access_of(user_id)  # layer 1: OpenFGA reachable for this user (503 otherwise)
        try:
            checked = sql_guard.check(body.sql)
        except Refused as e:
            extra = {"position": e.position} if e.position is not None else {}
            return refuse(422, "refused", e.message, **extra)
        today = utcnow().astimezone(UTC).date()
        try:
            used = usage.used(user_id, today)
        except psycopg.Error as e:
            log.warning("sql usage unavailable: %s", type(e).__name__)
            raise HTTPException(503, "the SQL workspace is unavailable") from None
        if used >= limits.seconds_per_day:
            return refuse(429, "budget", f"you have used today's {limits.seconds_per_day:,}"
                          " seconds of queries; it renews at midnight UTC", budget=budget(used))
        spent = 0.0
        try:
            result = store.run_sql(user_id, checked, limits)
            spent = result.seconds
        except Busy as e:
            message = ("a query of yours is still running: wait for it" if e.kind == "user" else
                       "the workspace is busy with other queries: try again in a moment")
            return refuse(429, f"busy_{e.kind}", message)
        except TooCostly as e:
            why = (f"one step expects about {e.rows:,.0f} rows, more than {limits.max_plan_rows:,.0f}:"
                   " a join without a condition?" if e.rows > limits.max_plan_rows else
                   f"its estimated cost is {e.cost:,.0f}, over {limits.max_cost:,.0f}")
            return refuse(422, "too_costly", f"the plan is too large to run here ({why}); filter"
                          " on company_id, metric or dates, or read latest_figures",
                          cost=round(e.cost), rows=round(e.rows))
        except Failed as e:
            spent = e.seconds
            log.info("sql failed", extra={"user_id": user_id, "sqlstate": e.sqlstate,
                                          "code": e.code})
            return refuse(422, e.code, e.message)
        except Tampered:
            log.error("sql session changed under a query", extra={"user_id": user_id})
            raise HTTPException(500, "the query was stopped") from None
        except psycopg.Error as e:
            log.warning("sql unavailable: %s", type(e).__name__, extra={"user_id": user_id})
            raise HTTPException(503, "the SQL workspace is unavailable") from None
        finally:
            if spent:
                try:
                    usage.add(user_id, today, spent)
                except psycopg.Error as e:
                    log.warning("sql usage not counted: %s", type(e).__name__)
        log.info("sql ran", extra={"user_id": user_id, "rows": len(result.rows),
                                   "seconds": round(result.seconds, 3), "cost": result.cost})
        return {"columns": result.columns, "rows": result.rows,
                "row_count": len(result.rows), "truncated": result.truncated,
                "max_rows": MAX_ROWS, "seconds": round(result.seconds, 3),
                "cost": round(result.cost), "reads": result.relations, "sql": checked.sql,
                "budget": budget(used + result.seconds)}
