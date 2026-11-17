"""Your data (B2c): a user's own time series by company and date, uploaded as CSV and joined to
Tributary's figures in formulas (`mine.<dataset>.<field>`, formula/check.py), charts, own metrics
in the screener (screener.py) and a panel on the company page. Tables: tributary migration 100
(schema `userdata`); the file's rules: datasets_csv.py.

Owner-only, like the portfolio: every statement runs as query_api with app.user_id set (forced
row-level security keyed on it) AND filters on the JWT `sub` the route passes in. Another user's
dataset and one that doesn't exist get the same 404; a formula naming another user's dataset reads
"You have no dataset" exactly like a typo. Nothing here grants anything: a user's rows sit beside
gold, never in it, and every read of Tributary's own figures still goes through require() and 030's
policies. A user's data is never shared, never mixed into another user's answers or Tributary's
public figures (status, extremes, peers' medians), and never sent to the assistant except by the
user's own formulas.

Uploads (POST /v1/datasets, /v1/datasets/{id}/upload): multipart/form-data with one `file` and
plain fields, read by hand (no 422 echoes a value). `dry_run=true` previews: what would be stored,
the rows whose company matched no ticker or CIK (by row number), nothing written. A company is
resolved exactly through silver.resolve_company (a CIK, or a ticker: the one company it is now,
else the one it was), never by name and never a guess between two. A row is known from its
declared publication date, else from the upload day (US Eastern): point in time like B1, a value
is never read as of a day before it could have been known. `mode=append` adds a later file to a
dataset (same fields): a row whose values equal the version already known is not stored again, so
only real revisions become versions.

Limits (LIMITS, also in GET /v1/datasets): a file 20 MB, 200,000 rows, 50 columns; 50 datasets and
200 MB of uploads per user; 30 upload requests an hour, 10 of them stored; one upload read at a
time per process (memory); and USER_DATA_SPACE_MB across everyone (the disk: a refusal names no
number). Demo sandboxes can't upload (a day-long anonymous user; the same rule as webhooks).

No `from __future__ import annotations` here: FastAPI must see the route parameters' real types
(the signed-in `User` dependency is a local alias)."""

import logging
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from query_api import datasets_csv as csvfile
from query_api import point_in_time
from query_api.formula.check import DatasetInfo
from query_api.sandbox import is_sandbox_user

log = logging.getLogger("query_api.datasets")

MAX_DATASETS = 50
MAX_USER_BYTES = 200 * 2**20
REQUESTS_PER_HOUR = 30  # previews and uploads
UPLOADS_PER_HOUR = 10   # of them, stored
DEFAULT_SPACE_MB = 4096  # all users' rows on disk; USER_DATA_SPACE_MB
FORM_FIELDS = frozenset({"file", "name", "title", "company_column", "date_column",
                         "published_column", "units", "currency", "decimal", "mode", "dry_run"})
MAX_FORM_FIELD = 4_096  # characters of any plain field
LEFT_OUT_LISTED = 100   # row numbers of unresolved rows in an answer
PANEL_ROWS = 12         # the company page's latest days per dataset
MAX_PAGE = 1_000        # rows a page of GET /v1/datasets/{id}/rows
INSERT_CHUNK = 5_000
QUERY_TIMEOUT = "5s"
UPLOAD_TIMEOUT = "60s"
TITLE_MAX = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
NOT_FOUND = "dataset not found"

LIMITS = {"file_bytes": csvfile.MAX_BYTES, "rows": csvfile.MAX_ROWS,
          "columns": csvfile.MAX_COLUMNS, "datasets": MAX_DATASETS,
          "user_bytes": MAX_USER_BYTES, "requests_per_hour": REQUESTS_PER_HOUR,
          "uploads_per_hour": UPLOADS_PER_HOUR}


class Refusal(Exception):
    """An upload or change the rules refuse: an HTTP status, a code and a fixed message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class Upload:
    """What an upload asks for, checked (the form's plain fields)."""
    name: str
    title: str
    options: csvfile.Options
    currency: str
    mode: Literal["replace", "append"]
    dry_run: bool


class UserData(Protocol):
    def catalog(self, user_id: str, names: list[str] | None = None) -> dict[str, DatasetInfo]: ...
    def all(self, user_id: str) -> list[dict]: ...
    def get(self, user_id: str, dataset_id: str) -> dict | None: ...
    def usage(self, user_id: str) -> dict: ...
    def take_request(self, user_id: str, data_bytes: int, stored: bool) -> None: ...
    def resolve(self, user_id: str, cells: list[str]) -> dict[str, str]: ...
    def save(self, user_id: str, dataset_id: str | None, upload: Upload,
             scan: csvfile.Scan, rows: Iterator[tuple[str, date, date, list[str | None]]],
             data_bytes: int) -> dict: ...
    def update(self, user_id: str, dataset_id: str, title: str | None,
               units: dict[str, str] | None, currency: str | None) -> dict | None: ...
    def delete(self, user_id: str, dataset_id: str) -> bool: ...
    def rows(self, user_id: str, dataset_id: str, company_id: str | None, start: date | None,
             end: date | None, as_of: date | None, versions: bool, limit: int,
             offset: int) -> list[dict] | None: ...
    def company_data(self, user_id: str, company_id: str, as_of: date | None) -> list[dict]: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


SUMMARY = ("d.dataset_id::text, d.name, d.title, d.fields, d.units, d.currency, d.known_from,"
           " d.row_count, d.value_count, d.company_count, d.bytes, d.first_day, d.last_day,"
           " d.created_at, d.uploaded_at")


def summary(row: Mapping[str, Any]) -> dict:
    """A dataset as the API answers it."""
    out = {k: row[k] for k in ("dataset_id", "name", "title", "currency", "known_from",
                               "first_day", "last_day", "created_at", "uploaded_at", "bytes")}
    out["fields"] = [{"name": f, "unit": u} for f, u in zip(row["fields"], row["units"],
                                                              strict=True)]
    out["rows"], out["values"] = row["row_count"], row["value_count"]
    out["companies"] = row["company_count"]
    return out


def _vals(values: list[str | None]) -> str:
    """A numeric[] literal from datasets_csv's normalized decimal strings (digits, sign, point,
    exponent: nothing that needs quoting) and None."""
    return "{" + ",".join("NULL" if v is None else v for v in values) + "}"


class AppUserData:
    """Every method is one transaction as query_api with app.user_id set and a `user_id =`
    filter; writes that count against a cap take a per-user advisory lock first."""

    def __init__(self, pool: Pool, space_bytes: int = DEFAULT_SPACE_MB * 2**20) -> None:
        self._pool = pool
        self._space = space_bytes

    @contextmanager
    def _tx(self, user_id: str, timeout: str = QUERY_TIMEOUT,
            lock: bool = False) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET LOCAL ROLE query_api")
            cur.execute(f"SET LOCAL statement_timeout = '{timeout}'")
            cur.execute("SET LOCAL jit = off")
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            if lock:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("userdata:" + user_id,))
            try:
                yield cur
            finally:
                if conn.info.transaction_status != TransactionStatus.INERROR:
                    cur.execute("RESET ROLE")
                    cur.execute("SELECT set_config('app.user_id', '', true)")

    def catalog(self, user_id: str, names: list[str] | None = None) -> dict[str, DatasetInfo]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT dataset_id::text, name, fields, units, currency"
                        " FROM userdata.datasets WHERE user_id = %s"
                        " AND (%s::text[] IS NULL OR name = ANY(%s::text[]))",
                        (user_id, names, names))
            return {r["name"]: DatasetInfo(r["dataset_id"], r["name"], tuple(r["fields"]),
                                           tuple(r["units"]), r["currency"])
                    for r in cur.fetchall()}

    def all(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {SUMMARY} FROM userdata.datasets d WHERE d.user_id = %s"
                        " ORDER BY d.name", (user_id,))
            return [summary(r) for r in cur.fetchall()]

    def get(self, user_id: str, dataset_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {SUMMARY} FROM userdata.datasets d"
                        " WHERE d.dataset_id = %s AND d.user_id = %s", (dataset_id, user_id))
            row = cur.fetchone()
            return summary(row) if row else None

    def usage(self, user_id: str) -> dict:
        with self._tx(user_id) as cur:
            return self._usage(cur, user_id)

    def _usage(self, cur: psycopg.Cursor, user_id: str) -> dict:
        cur.execute("SELECT (SELECT count(*) FROM userdata.datasets WHERE user_id = %(u)s)"
                    "         AS datasets,"
                    " (SELECT coalesce(sum(bytes), 0) FROM userdata.datasets"
                    "  WHERE user_id = %(u)s) AS bytes,"
                    " (SELECT count(*) FROM userdata.uploads WHERE user_id = %(u)s"
                    "  AND uploaded_at > statement_timestamp() - interval '1 hour') AS requests,"
                    " (SELECT count(*) FROM userdata.uploads WHERE user_id = %(u)s AND stored"
                    "  AND uploaded_at > statement_timestamp() - interval '1 hour') AS uploads",
                    {"u": user_id})
        row = cur.fetchone()
        assert row is not None
        return {k: int(v) for k, v in row.items()}

    def take_request(self, user_id: str, data_bytes: int, stored: bool) -> None:
        """Counts an upload request (a preview or a store) in the hour, or Refusal past the
        limits. Before the file is read: a refused request costs no parsing."""
        with self._tx(user_id, lock=True) as cur:
            used = self._usage(cur, user_id)
            if used["requests"] >= REQUESTS_PER_HOUR or (
                    stored and used["uploads"] >= UPLOADS_PER_HOUR):
                raise Refusal(429, "too_many", "That's the hour's uploads: try again later "
                              f"({UPLOADS_PER_HOUR} stored and {REQUESTS_PER_HOUR} previews and "
                              "uploads an hour)")
            cur.execute("DELETE FROM userdata.uploads WHERE user_id = %s"
                        " AND uploaded_at < statement_timestamp() - interval '1 day'", (user_id,))
            cur.execute("INSERT INTO userdata.uploads (user_id, stored, bytes)"
                        " VALUES (%s, %s, %s)", (user_id, stored, data_bytes))

    def resolve(self, user_id: str, cells: list[str]) -> dict[str, str]:
        """cell → company_id for the cells that are exactly a CIK or a ticker
        (silver.resolve_company: public identity): the one company it is now, else the one
        company it was; a ticker two companies hold is no match, and a name never is."""
        if not cells:
            return {}
        with self._tx(user_id, UPLOAD_TIMEOUT) as cur:
            cur.execute(
                "SELECT t.cell, m.company_id FROM unnest(%s::text[]) t(cell)"
                " CROSS JOIN LATERAL ("
                "  SELECT CASE WHEN count(DISTINCT r.company_id) FILTER (WHERE r.is_current) = 1"
                "              THEN min(r.company_id) FILTER (WHERE r.is_current)"
                "              WHEN count(DISTINCT r.company_id) FILTER (WHERE r.is_current) = 0"
                "               AND count(DISTINCT r.company_id) = 1 THEN min(r.company_id)"
                "         END AS company_id"
                "  FROM silver.resolve_company(t.cell) r"
                "  WHERE r.matched_on IN ('cik', 'ticker')) m", (cells,))
            return {r["cell"]: r["company_id"] for r in cur.fetchall() if r["company_id"]}

    def save(self, user_id: str, dataset_id: str | None, upload: Upload,
             scan: csvfile.Scan, rows: Iterator[tuple[str, date, date, list[str | None]]],
             data_bytes: int) -> dict:
        """Stores a checked file: a new dataset (dataset_id None), or into one (replace: its
        rows become the file's; append: the file's rows are added, unchanged ones skipped).
        Refusal on a limit, a taken name or an append that doesn't fit."""
        known_from = "column" if scan.published is not None else "upload"
        fields = [name for name, _ in scan.fields]
        with self._tx(user_id, UPLOAD_TIMEOUT, lock=True) as cur:
            used = self._usage(cur, user_id)
            cur.execute("SELECT pg_total_relation_size('userdata.rows')"
                        " + pg_total_relation_size('userdata.datasets') AS space")
            row = cur.fetchone()
            if row is not None and row["space"] >= self._space:
                raise Refusal(507, "full", "Tributary has no room for more uploads just now")
            if dataset_id is None:
                if used["datasets"] >= MAX_DATASETS:
                    raise Refusal(409, "limit", f"You can keep at most {MAX_DATASETS} "
                                  "datasets: delete one first")
                if used["bytes"] + data_bytes > MAX_USER_BYTES:
                    raise Refusal(409, "limit", f"Your uploads can total at most "
                                  f"{MAX_USER_BYTES // 2**20} MB: delete a dataset first")
                try:
                    with cur.connection.transaction():
                        cur.execute("INSERT INTO userdata.datasets (user_id, name, title, fields,"
                                    " units, currency, known_from, bytes)"
                                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                                    " RETURNING dataset_id::text",
                                    (user_id, upload.name, upload.title, fields, scan.units,
                                     upload.currency, known_from, data_bytes))
                except psycopg.errors.UniqueViolation:
                    raise Refusal(409, "name_taken", "You already have a dataset with that "
                                  "name: upload into it, or choose another name") from None
                created = cur.fetchone()
                assert created is not None
                dataset_id = created["dataset_id"]
                order = list(range(len(fields)))
            else:
                cur.execute("SELECT fields, units, known_from, bytes FROM userdata.datasets"
                            " WHERE dataset_id = %s AND user_id = %s FOR UPDATE",
                            (dataset_id, user_id))
                old = cur.fetchone()
                if old is None:
                    raise Refusal(404, "not_found", NOT_FOUND)
                kept = old["bytes"] if upload.mode == "append" else 0
                if used["bytes"] - old["bytes"] + kept + data_bytes > MAX_USER_BYTES:
                    raise Refusal(409, "limit", f"Your uploads can total at most "
                                  f"{MAX_USER_BYTES // 2**20} MB: delete a dataset first")
                if upload.mode == "append":
                    if sorted(old["fields"]) != sorted(fields):
                        raise Refusal(422, "fields_differ", "To add to a dataset, the file "
                                      "needs the same value columns; replace it instead")
                    if old["known_from"] != known_from:
                        raise Refusal(422, "known_from_differs", "This dataset's rows are "
                                      "known from " + ("a publication date column"
                                                       if old["known_from"] == "column"
                                                       else "the upload day")
                                      + ": upload the same way, or replace it")
                    order = [fields.index(f) for f in old["fields"]]
                    units = [upload.options.units.get(f, u)
                             for f, u in zip(old["fields"], old["units"], strict=True)]
                    cur.execute("UPDATE userdata.datasets SET units = %s, bytes = bytes + %s"
                                " WHERE dataset_id = %s AND user_id = %s",
                                (units, data_bytes, dataset_id, user_id))
                else:
                    order = list(range(len(fields)))
                    cur.execute("DELETE FROM userdata.rows WHERE dataset_id = %s"
                                " AND user_id = %s", (dataset_id, user_id))
                    cur.execute("UPDATE userdata.datasets SET fields = %s, units = %s,"
                                " currency = %s, known_from = %s, bytes = %s"
                                " WHERE dataset_id = %s AND user_id = %s",
                                (fields, scan.units, upload.currency, known_from, data_bytes,
                                 dataset_id, user_id))
            stored = unchanged = 0
            for chunk in _chunks(rows, INSERT_CHUNK):
                companies = [c for c, _, _, _ in chunk]
                days = [d for _, d, _, _ in chunk]
                known = [k for _, _, k, _ in chunk]
                vals = [_vals([v[i] for i in order]) for _, _, _, v in chunk]
                if upload.mode == "append" and dataset_id is not None:
                    # a row equal to the version known before it is no revision: not stored
                    cur.execute(
                        "INSERT INTO userdata.rows (dataset_id, user_id, company_id, day,"
                        " known_at, vals)"
                        " SELECT %(d)s, %(u)s, t.c, t.day, t.k, t.v::numeric[]"
                        " FROM unnest(%(c)s::text[], %(days)s::date[], %(k)s::date[],"
                        "             %(v)s::text[]) t(c, day, k, v)"
                        " WHERE NOT EXISTS (SELECT 1 FROM (SELECT r.vals FROM userdata.rows r"
                        "   WHERE r.dataset_id = %(d)s AND r.company_id = t.c AND r.day = t.day"
                        "     AND r.known_at < t.k ORDER BY r.known_at DESC LIMIT 1) p"
                        "   WHERE p.vals = t.v::numeric[])"
                        " ON CONFLICT (dataset_id, company_id, day, known_at)"
                        " DO UPDATE SET vals = EXCLUDED.vals",
                        {"d": dataset_id, "u": user_id, "c": companies, "days": days,
                         "k": known, "v": vals})
                else:
                    cur.execute(
                        "INSERT INTO userdata.rows (dataset_id, user_id, company_id, day,"
                        " known_at, vals)"
                        " SELECT %(d)s, %(u)s, t.c, t.day, t.k, t.v::numeric[]"
                        " FROM unnest(%(c)s::text[], %(days)s::date[], %(k)s::date[],"
                        "             %(v)s::text[]) t(c, day, k, v)",
                        {"d": dataset_id, "u": user_id, "c": companies, "days": days,
                         "k": known, "v": vals})
                stored += cur.rowcount
                unchanged += len(chunk) - cur.rowcount
            cur.execute(
                "UPDATE userdata.datasets d SET row_count = s.n, value_count = s.v,"
                " company_count = s.c, first_day = s.f, last_day = s.l,"
                " title = %(t)s, uploaded_at = statement_timestamp()"
                " FROM (SELECT count(*) AS n,"
                "       coalesce(sum(cardinality(array_remove(vals, NULL))), 0) AS v,"
                "       count(DISTINCT company_id) AS c, min(day) AS f, max(day) AS l"
                "       FROM userdata.rows WHERE dataset_id = %(d)s AND user_id = %(u)s) s"
                " WHERE d.dataset_id = %(d)s AND d.user_id = %(u)s"
                f" RETURNING {SUMMARY}",
                {"d": dataset_id, "u": user_id, "t": upload.title})
            out = cur.fetchone()
            assert out is not None
            return {"dataset": summary(out), "stored": stored, "unchanged": unchanged}

    def update(self, user_id: str, dataset_id: str, title: str | None,
               units: dict[str, str] | None, currency: str | None) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute("SELECT fields, units FROM userdata.datasets"
                        " WHERE dataset_id = %s AND user_id = %s FOR UPDATE",
                        (dataset_id, user_id))
            old = cur.fetchone()
            if old is None:
                return None
            if units and set(units) - set(old["fields"]):
                raise Refusal(422, "units", "The units name a field the dataset doesn't have")
            new_units = [(units or {}).get(f, u)
                         for f, u in zip(old["fields"], old["units"], strict=True)]
            cur.execute("UPDATE userdata.datasets d SET title = coalesce(%s, d.title),"
                        " units = %s, currency = coalesce(%s, d.currency)"
                        " WHERE d.dataset_id = %s AND d.user_id = %s"
                        f" RETURNING {SUMMARY}",
                        (title, new_units, currency, dataset_id, user_id))
            row = cur.fetchone()
            return summary(row) if row else None

    def delete(self, user_id: str, dataset_id: str) -> bool:
        with self._tx(user_id, UPLOAD_TIMEOUT) as cur:
            cur.execute("DELETE FROM userdata.datasets WHERE dataset_id = %s AND user_id = %s",
                        (dataset_id, user_id))
            return cur.rowcount == 1

    def rows(self, user_id: str, dataset_id: str, company_id: str | None, start: date | None,
             end: date | None, as_of: date | None, versions: bool, limit: int,
             offset: int) -> list[dict] | None:
        """A page of a dataset's rows by company and day: the version known at the end of
        as_of (the latest without it), or every version (`versions`). None: no such dataset."""
        with self._tx(user_id) as cur:
            cur.execute("SELECT fields FROM userdata.datasets"
                        " WHERE dataset_id = %s AND user_id = %s", (dataset_id, user_id))
            ds = cur.fetchone()
            if ds is None:
                return None
            pick = "" if versions else "DISTINCT ON (r.company_id, r.day)"
            cur.execute(
                f"SELECT {pick} r.company_id, r.day, r.known_at, r.vals"
                " FROM userdata.rows r"
                " WHERE r.dataset_id = %(d)s AND r.user_id = %(u)s"
                "   AND (%(c)s::text IS NULL OR r.company_id = %(c)s)"
                "   AND (%(s)s::date IS NULL OR r.day >= %(s)s)"
                "   AND (%(e)s::date IS NULL OR r.day <= %(e)s)"
                "   AND (%(a)s::date IS NULL OR r.known_at <= %(a)s)"
                " ORDER BY r.company_id, r.day DESC, r.known_at DESC"
                " LIMIT %(l)s OFFSET %(o)s",
                {"d": dataset_id, "u": user_id, "c": company_id, "s": start, "e": end,
                 "a": as_of, "l": limit, "o": offset})
            return [{"company_id": r["company_id"], "day": r["day"], "known_at": r["known_at"],
                     "values": _values(ds["fields"], r["vals"])} for r in cur.fetchall()]

    def company_data(self, user_id: str, company_id: str, as_of: date | None) -> list[dict]:
        """The caller's datasets with rows for this company: each with its latest PANEL_ROWS
        days, the version known at the end of as_of."""
        with self._tx(user_id) as cur:
            cur.execute(
                f"SELECT {SUMMARY}, r.day, r.known_at, r.vals"
                " FROM userdata.datasets d"
                " CROSS JOIN LATERAL (SELECT DISTINCT ON (x.day) x.day, x.known_at, x.vals"
                "   FROM userdata.rows x WHERE x.dataset_id = d.dataset_id AND x.user_id = %(u)s"
                "    AND x.company_id = %(c)s AND (%(a)s::date IS NULL OR x.known_at <= %(a)s)"
                "   ORDER BY x.day DESC, x.known_at DESC LIMIT %(n)s) r"
                " WHERE d.user_id = %(u)s ORDER BY d.name, r.day DESC",
                {"u": user_id, "c": company_id, "a": as_of, "n": PANEL_ROWS})
            out: dict[str, dict] = {}
            for r in cur.fetchall():
                ds = out.setdefault(r["dataset_id"], {**summary(r), "rows_here": []})
                ds["rows_here"].append({"day": r["day"], "known_at": r["known_at"],
                                        "values": _values(r["fields"], r["vals"])})
            return list(out.values())


def _values(fields: list[str], vals: list[Any]) -> dict[str, str | None]:
    """Exact decimal strings by field name (like segments' values)."""
    return {f: (None if v is None else str(v)) for f, v in zip(fields, vals, strict=False)}


def _chunks(rows: Iterator[Any], size: int) -> Iterator[list[Any]]:
    chunk = []
    for r in rows:
        chunk.append(r)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def space_from_env(env: Mapping[str, str] = os.environ) -> int:
    """USER_DATA_SPACE_MB (default DEFAULT_SPACE_MB) as bytes."""
    mb = int(env.get("USER_DATA_SPACE_MB") or DEFAULT_SPACE_MB)
    if mb < 1:
        raise ValueError("USER_DATA_SPACE_MB must be at least 1")
    return mb * 2**20


# --- the upload's form, read by hand -----------------------------------------------------------

def _bad(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code, **extra}, status_code=status)


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def read_upload(fields: Mapping[str, str], *, creating: bool,
                default_name: str | None = None) -> Upload:
    """The plain fields of an upload, checked: Refusal(422) with a fixed message on a bad one."""
    unknown = set(fields) - FORM_FIELDS
    if unknown:
        raise Refusal(422, "invalid", "The form has a field uploads don't take: file, name, "
                      "title, company_column, date_column, published_column, units, currency, "
                      "decimal, mode, dry_run")
    if any(len(v) > MAX_FORM_FIELD for v in fields.values()):
        raise Refusal(422, "invalid", f"A form field is longer than {MAX_FORM_FIELD} characters")
    raw_name = fields.get("name") or default_name or ""
    name = csvfile.simplify(raw_name)
    if creating and not name:
        raise Refusal(422, "name", "Name the dataset (letters, digits and _): formulas read it "
                      "as mine.<name>.<field>")
    title = (fields.get("title") or "").strip() or raw_name.strip() or name
    if len(title) > TITLE_MAX or _CONTROL.search(title):
        raise Refusal(422, "title", f"A title is at most {TITLE_MAX} characters, without "
                      "control characters")
    currency = (fields.get("currency") or "USD").strip().upper()
    if not _CURRENCY.match(currency):
        raise Refusal(422, "currency", "A currency is a three-letter code such as USD or EUR")
    decimal = (fields.get("decimal") or "").strip() or None
    if decimal not in (None, ".", ","):
        raise Refusal(422, "decimal", 'The decimal mark is "." or ","')
    mode = (fields.get("mode") or "replace").strip()
    if mode not in ("replace", "append") or (creating and mode != "replace"):
        raise Refusal(422, "mode", "mode is replace or append (into an existing dataset)")
    try:
        units = csvfile.parse_units(fields.get("units"))
    except ValueError as e:
        raise Refusal(422, "units", str(e)) from None

    def column(key: str) -> str | None:
        return (fields.get(key) or "").strip() or None

    return Upload(name=name, title=title, currency=currency, mode=mode,  # type: ignore[arg-type]
                  dry_run=_flag(fields.get("dry_run")),
                  options=csvfile.Options(company_column=column("company_column"),
                                          date_column=column("date_column"),
                                          published_column=column("published_column"),
                                          decimal=decimal, units=units))


async def _form(request: Request) -> tuple[bytes, dict[str, str], int]:
    """(the file's bytes, the plain fields, the body's size) of a multipart upload, bounded
    before anything is read; Refusal otherwise."""
    size = request.headers.get("content-length")
    limit = csvfile.MAX_BYTES + 64 * 1024
    if size is None or not size.isdigit():
        raise Refusal(411, "length", "Send the file as multipart/form-data with a "
                      "Content-Length")
    if int(size) > limit:
        raise Refusal(413, "too_large", f"The file is larger than "
                      f"{csvfile.MAX_BYTES // 2**20} MB")
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise Refusal(415, "multipart", "Send the file as multipart/form-data, the CSV in a "
                      "field named file")
    try:
        form = await request.form(max_files=1, max_fields=len(FORM_FIELDS),
                                  max_part_size=MAX_FORM_FIELD * 4)
    except StarletteHTTPException:
        raise Refusal(400, "multipart", "The form isn't readable multipart/form-data with one "
                      "file") from None
    try:
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise Refusal(422, "file", "Send the CSV in a field named file")
        data = await upload.read(csvfile.MAX_BYTES + 1)
        fields: dict[str, str] = {}
        for key, value in form.multi_items():
            if key == "file":
                continue
            if not isinstance(value, str) or key in fields:
                raise Refusal(422, "invalid", "Send one file and each plain field once")
            fields[key] = value
    finally:
        await form.close()
    if len(data) > csvfile.MAX_BYTES:
        raise Refusal(413, "too_large", f"The file is larger than "
                      f"{csvfile.MAX_BYTES // 2**20} MB")
    return data, fields, int(size)


# --- routes -------------------------------------------------------------------------------------

DatasetId = Annotated[UUID, Path(description="a dataset_id")]
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]


class DatasetChange(BaseModel):
    """PATCH /v1/datasets/{id}: its title, a field's unit or the currency of its money."""
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=TITLE_MAX)
    units: dict[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")],
                Literal["number", "ratio", "money", "shares", "per_share"]] | None = Field(
        None, max_length=csvfile.MAX_FIELDS)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")


def add_dataset_routes(app: FastAPI, *, data: UserData | None, current_user: Callable[..., str],
                       today: Callable[[], date]) -> None:
    """The /v1/datasets routes and the company page's panel. Without a database (`data` None)
    every one answers 503."""
    User = Annotated[str, Depends(current_user)]
    reading = threading.BoundedSemaphore(1)  # one file in memory at a time (the pod's limit)

    def store() -> UserData:
        if data is None:
            raise HTTPException(503, "your data is unavailable")
        return data

    def upload(user_id: str, dataset_id: str | None, raw: bytes, form: Mapping[str, str],
               size: int) -> dict:
        creating = dataset_id is None
        current = None
        if not creating:
            assert dataset_id is not None
            current = store().get(user_id, dataset_id)
            if current is None:
                raise Refusal(404, "not_found", NOT_FOUND)
        up = read_upload(form, creating=creating,
                         default_name=None if creating else (current or {}).get("name"))
        if not creating and not (form.get("title") or "").strip():
            up = Upload(up.name, (current or {})["title"], up.options, up.currency, up.mode,
                        up.dry_run)
        store().take_request(user_id, size, stored=not up.dry_run)
        if not reading.acquire(timeout=30):
            raise Refusal(503, "busy", "Another upload is being read: try again in a moment")
        try:
            day = today()
            try:
                scan = csvfile.scan(raw, up.options, day)
            except csvfile.Refused as e:
                raise _Refused(e) from None
            cells = sorted(scan.companies)
            resolved = store().resolve(user_id, cells)
            left_out = [k[0] for k in scan.keys if k[1] not in resolved]
            seen: dict[tuple[str, date, date], int] = {}
            dupes: list[csvfile.Problem] = []
            for row_no, cell, when, known in scan.keys:
                if cell not in resolved:
                    continue
                key = (resolved[cell], when, known or day)
                if key in seen:
                    dupes.append(csvfile.Problem(
                        "same_row", f"Rows {seen[key]} and {row_no} are the same company and "
                        "date" + (" and publication date" if known else ""), row_no))
                else:
                    seen[key] = row_no
            if dupes:
                raise _Refused(csvfile.Refused(dupes, len(dupes)))
            answer: dict[str, Any] = {
                "columns": {"company": scan.header[scan.company], "date": scan.header[scan.day],
                            "published": (scan.header[scan.published]
                                          if scan.published is not None else None)},
                "suggested": scan.suggested,
                "delimiter": {",": "comma", ";": "semicolon", "\t": "tab"}[scan.delimiter],
                "decimal": scan.decimal,
                "left_out": {"rows": left_out[:LEFT_OUT_LISTED], "count": len(left_out),
                             "companies": len({k[1] for k in scan.keys
                                               if k[1] not in resolved})},
                "blank_rows": scan.blank_rows,
                "limits": LIMITS,
            }
            if not seen:
                raise _Refused(csvfile.Refused([csvfile.Problem(
                    "no_companies", "No row's company is a ticker or CIK Tributary knows")]))
            if up.dry_run:
                kept = [k for k in scan.keys if k[1] in resolved]
                return {"preview": True, "name": up.name, "title": up.title,
                        "fields": [{"name": n, "unit": u} for (n, _), u in
                                   zip(scan.fields, scan.units, strict=True)],
                        "rows": len(kept), "companies": len({resolved[k[1]] for k in kept}),
                        "first_day": min(k[2] for k in kept), "last_day": max(k[2] for k in kept),
                        "known_from": "column" if scan.published is not None else "upload",
                        "usage": store().usage(user_id), **answer}
            keys = {k[0]: k for k in scan.keys if k[1] in resolved}

            def rows() -> Iterator[tuple[str, date, date, list[str | None]]]:
                for row_no, values in csvfile.values(scan):
                    k = keys.get(row_no)
                    if k is not None:
                        yield resolved[k[1]], k[2], k[3] or day, values

            saved = store().save(user_id, dataset_id, up, scan, rows(), len(raw))
            log.info("dataset stored", extra={"user_id": user_id,
                                              "dataset_id": saved["dataset"]["dataset_id"],
                                              "rows": saved["stored"]})
            return {"preview": False, **saved, **answer}
        finally:
            reading.release()

    async def receive(request: Request, user_id: str, dataset_id: str | None):
        if is_sandbox_user(user_id):
            return _bad(403, "permanent_account", "Your own data needs a permanent account: "
                                                  "sign in with GitHub or with an account")
        try:
            raw, form, size = await _form(request)
            return await run_in_threadpool(upload, user_id, dataset_id, raw, form, size)
        except _Refused as e:
            return JSONResponse({"detail": "The file can't be read as a dataset",
                                 "code": "file", **e.refused.to_dict()}, status_code=422)
        except Refusal as e:
            return _bad(e.status, e.code, e.message)
        except psycopg.errors.QueryCanceled:
            return _bad(503, "too_slow", "Storing the file took too long: try a smaller one")

    @app.get("/v1/datasets", response_model=dict)
    def list_datasets(user_id: User) -> dict:
        """Your datasets, by name, with their fields and sizes, and the upload limits."""
        return {"datasets": store().all(user_id), "limits": LIMITS,
                "uploads_allowed": not is_sandbox_user(user_id)}

    @app.post("/v1/datasets")
    async def create_dataset(request: Request, user_id: User):
        """multipart/form-data: `file` (CSV) and `name`, optionally title, company_column,
        date_column, published_column, units ("field:unit,..."), currency, decimal and dry_run
        (preview, store nothing). datasets.py and datasets_csv.py have the rules."""
        return await receive(request, user_id, None)

    @app.post("/v1/datasets/{dataset_id}/upload")
    async def upload_into(request: Request, user_id: User, dataset_id: DatasetId):
        """A later file into a dataset: `mode` replace (default: its rows become the file's) or
        append (same fields; unchanged rows skipped), the same form as creating one."""
        return await receive(request, user_id, str(dataset_id))

    @app.get("/v1/datasets/{dataset_id}", response_model=dict)
    def get_dataset(user_id: User, dataset_id: DatasetId) -> dict:
        row = store().get(user_id, str(dataset_id))
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    @app.patch("/v1/datasets/{dataset_id}", response_model=dict)
    def change_dataset(user_id: User, dataset_id: DatasetId, body: DatasetChange) -> Any:
        """Its title, units or currency; the rows stay as they are."""
        if body.title is not None and _CONTROL.search(body.title):
            raise HTTPException(422, "title: no control characters")
        try:
            row = store().update(user_id, str(dataset_id),
                                 body.title.strip() if body.title else None, body.units,
                                 body.currency)
        except Refusal as e:
            return _bad(e.status, e.code, e.message)
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    @app.delete("/v1/datasets/{dataset_id}", status_code=204)
    def delete_dataset(user_id: User, dataset_id: DatasetId) -> None:
        """Deletes it and its rows; formulas and metrics naming it then read "no dataset"."""
        if not store().delete(user_id, str(dataset_id)):
            raise HTTPException(404, NOT_FOUND)

    @app.get("/v1/datasets/{dataset_id}/rows", response_model=dict)
    def dataset_rows(
        user_id: User, dataset_id: DatasetId,
        company_id: Annotated[str | None, Query(pattern=r"^\d{10}$")] = None,
        start: Annotated[date | None, Query(alias="from", description="days on or after")]
            = None,
        end: Annotated[date | None, Query(alias="to", description="days on or before")] = None,
        as_of: Annotated[date | None, Query(
            description="a past day: each row as you knew it at its end (echoed as `known_on`)")]
            = None,
        versions: Annotated[bool, Query(description="every version of each row, newest "
                                                    "first, instead of the one known")] = False,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
        offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
    ) -> dict:
        """A page of a dataset's rows by company, newest day first, with exact values."""
        known_on = point_in_time.check(as_of, today())
        rows = store().rows(user_id, str(dataset_id), company_id, start, end, known_on,
                            versions, limit + 1, offset)
        if rows is None:
            raise HTTPException(404, NOT_FOUND)
        return {"dataset_id": str(dataset_id),
                **({"known_on": known_on} if known_on is not None else {}),
                "limit": limit, "offset": offset, "has_more": len(rows) > limit,
                "rows": rows[:limit]}

    @app.get("/v1/companies/{company_id}/datasets", response_model=dict)
    def company_datasets(
        user_id: User, company_id: CompanyId,
        as_of: Annotated[date | None, Query(
            description="a past day: your rows as you knew them at its end")] = None,
    ) -> dict:
        """Your own data on this company (the company page's "Your data"): each of your
        datasets with rows for it and its latest days. Only your rows: nothing here is
        Tributary's, so no tier is needed."""
        known_on = point_in_time.check(as_of, today())
        return {"company_id": company_id,
                **({"known_on": known_on} if known_on is not None else {}),
                "datasets": store().company_data(user_id, company_id, known_on)}


class _Refused(Exception):
    def __init__(self, refused: csvfile.Refused) -> None:
        super().__init__(refused.problems[0].message if refused.problems else "refused")
        self.refused = refused
