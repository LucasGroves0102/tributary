"""Filing alerts (F6, Phase 9 E): a user hears when a company they watch (their watchlist) or
analyse (an analysis project's company) files a 10-K, a 10-Q or an 8-K with a material item:
in the app (`/v1/alerts/*`, the web app's bell and Monitor step) and, optionally, at a webhook
of theirs (query_api/webhooks.py). `query-api-alerts` (query_api/alert_job.py, a CronJob every
ten minutes) makes them; this module holds the rules, the app-database storage and the routes.

Rules (the spec; README "Filing alerts"; test_alerts.py):

- Followed companies: the watchlist's (`watchlist`, since the day each was added) and each
  analysis project's company (`analysis`, since the project was made). A company followed both
  ways is one alert with both sources. Watching grants nothing: an alert is about a public
  filing, and its figures are read as the user, through layer 1 and RLS, or not at all.
- Forms: 10-K, 10-Q and 8-K, originals (amendments re-report). Per company the user may turn
  any of the three off (`alert_settings`; no row = all three). Only filings filed on or after
  the day the company was followed alert.
- An 8-K alerts when any of its items is material: anything but the routine ones of
  query_api.events (5.07 votes, 5.08 nominations, 9.01 exhibits alone). Its items come from
  the filing index, else from the item headings the parser found (only with the filings tier);
  until either has them the alert is pending, and after PENDING_DAYS without them it is shown
  with its items unknown. An 8-K whose items are all routine is `skipped`: kept, never shown.
- A 10-K or 10-Q is pending until gold has its figures (its recap's current period cites the
  filing), then ready with KEY_FIGURES for its period (the quarter for a 10-Q, the fiscal year
  for a 10-K: revenue with its change on a year earlier, gross margin, else operating margin,
  then diluted EPS, else net income for a 10-K; up to three) and the user's red lines for the
  company evaluated at that period. Without the financials tier: ready, no figures, no red
  lines (`figures_status` no_access). After PENDING_DAYS without figures: ready, `not_in_data`.
- Red lines: a formula in the formula language (query_api/formula: catalog metrics, + - * /,
  lags like revenue[-4q], ttm(), abs(); no ticker: it reads the alert's company), a view
  (quarter or ttm), an operator (< <= > >=) and a constant. `crossed` is the comparison's truth
  at the filing's period (the formula's point whose period end is within 7 days of it); no
  such point is `no_value`. A red line describes the trouble ("gross margin < 0.55"), so
  crossed = the alert says so. At most MAX_RED_LINES per company.
- Idempotent on the filing: one notification per (user, accession, company), inserted with ON
  CONFLICT DO NOTHING, so a run seeing a filing again (each run reads the last LOOKBACK_DAYS)
  changes nothing. A delivery is one per (webhook, notification). Notifications older than
  KEEP_DAYS are deleted by the job.
- Shown: the API strips figures and red-line values again for a company the user can no longer
  read financials for (they were read under the access the user had then).

Owner-only: app migration 016's tables have FORCEd row-level security on app.user_id; every
statement here also filters on the user (the JWT `sub` in the routes, the user being worked for
in the job). Another user's notification, red line, webhook or delivery and one that doesn't
exist get the same 404.
"""


import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Protocol

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from query_api import events
from query_api.authorizer import Readable
from query_api.connections import Limiter
from query_api.formula import FormulaError, analyze, compile_formula, parse
from query_api.formula.check import PERCENT
from query_api.shapes import filing_url
from query_api.webhooks import (
    FORMATS,
    HttpsSender,
    Sender,
    Sent,
    WebhookRefused,
    body_of,
    headers,
    mask,
    new_salt,
    suggest_format,
    vet,
    webhook_secret,
)

log = logging.getLogger("query_api.alerts")

FORMS: tuple[str, ...] = ("10-K", "10-Q", "8-K")
SOURCES = ("watchlist", "analysis")
LOOKBACK_DAYS = 3        # each run reads filings filed in the last 3 days (ET)
PENDING_DAYS = 3         # a filing waits this long for its figures or items
KEEP_DAYS = 180          # notifications older than this are deleted by the job
MAX_RED_LINES = 10       # per company
MAX_WEBHOOKS = 3         # per user
MAX_SETTINGS = 1000      # per user
MAX_LIST = 100
TEST_SENDS_PER_HOUR = 5
MAX_BODY = 4096          # the webhook routes' JSON bodies
PERIOD_TOLERANCE_DAYS = 7
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
Id = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
CIK = re.compile(r"^\d{10}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# what an alert reads from the filing's recap (ui.py's gold.filing_recap): the metrics of
# KEY_FIGURES, with the span a form reports
RECAP_METRICS = ["revenue", "gross_margin", "operating_margin", "eps_diluted", "net_income"]
KEY_FIGURES: tuple[tuple[str, ...], ...] = (
    ("revenue",), ("gross_margin", "operating_margin"), ("eps_diluted", "net_income"))
FIGURE_LABELS = {"revenue": "Revenue", "gross_margin": "Gross margin",
                 "operating_margin": "Operating margin", "eps_diluted": "Diluted EPS",
                 "net_income": "Net income"}
OPS = ("<", "<=", ">", ">=")


# --- rules ------------------------------------------------------------------------------------

def material(items: list[str] | None) -> bool | None:
    """An 8-K's items: True when any isn't routine, False when all are, None when unknown."""
    if not items:
        return None
    return any(i not in events.ROUTINE for i in items)


def item_info(items: list[str] | None) -> list[dict]:
    return [{"item": i, "title": events.ITEMS.get(i, ("Item " + i, "other"))[0],
             "kind": events.ITEMS.get(i, ("", "other"))[1]} for i in items or []]


def _cites(row: dict, accession_no: str) -> bool:
    return row.get("accession_no") == accession_no or accession_no in (
        row.get("source_accessions") or [])


def key_figures(form: str, rows: list[dict], accession_no: str) -> list[dict] | None:
    """The alert's figures from a filing's recap rows (Store.recap), or None while gold doesn't
    have the filing yet (no current row cites it). Up to three, KEY_FIGURES' order."""
    span = "ttm" if form == "10-K" else "quarter"
    current = {r["metric"]: r for r in rows if r["comparison"] == "current" and r["span"] == span}
    if not any(_cites(r, accession_no) for r in current.values()):
        return None
    year_ago = {r["metric"]: r for r in rows
                if r["comparison"] == "year_ago" and r["span"] == span}
    out = []
    for choices in KEY_FIGURES:
        metric = next((m for m in choices if m in current and current[m]["value"] is not None
                       and not (m == "eps_diluted" and form == "10-K")), None)
        if metric is None:
            continue
        cur, ago = current[metric], year_ago.get(metric)
        entry: dict[str, Any] = {
            "metric": metric, "label": FIGURE_LABELS[metric], "span": span,
            "period_end": cur["period_end"].isoformat(), "value": str(cur["value"]),
            "unit": cur["unit"], "not_meaningful": cur.get("not_meaningful"),
            "year_ago": str(ago["value"]) if ago and ago["value"] is not None else None,
            "change": None, "pct_change": None}
        if ago and ago["value"] is not None:
            change = Decimal(cur["value"]) - Decimal(ago["value"])
            entry["change"] = str(change)
            if cur["unit"] != "ratio" and ago["value"] != 0:
                entry["pct_change"] = str((change / abs(Decimal(ago["value"])))
                                          .quantize(Decimal("0.0001")))
        out.append(entry)
    return out


def check_formula(formula: str, view: str) -> str | None:
    """Why a red line's formula can't be used (None: it can): the formula language's own
    problems, or a ticker (a red line reads the alert's company only)."""
    try:
        a = analyze(parse(formula), view)
    except FormulaError as e:
        return e.message
    if a.tickers:
        return "a red line reads its own company's figures: leave out the ticker"
    return None


def display_of(formula: str, view: str) -> str | None:
    """percent / multiple / number for a dimensionless formula (formula/check.py), else None."""
    try:
        return analyze(parse(formula), view).display
    except FormulaError:
        return None


def crossed(value: Decimal, op: str, threshold: Decimal) -> bool:
    return {"<": value < threshold, "<=": value <= threshold, ">": value > threshold,
            ">=": value >= threshold}[op]


def evaluate(rule: dict, points: list[dict], period_end: date | None) -> dict:
    """A red line on a formula's points (Store.formula rows, oldest first): at `period_end`
    (within PERIOD_TOLERANCE_DAYS) or, with None, at the latest point."""
    out = {"rule_id": str(rule["rule_id"]), "label": rule["label"], "formula": rule["formula"],
           "view": rule["view"], "op": rule["op"], "threshold": str(rule["threshold"]),
           "display": display_of(rule["formula"], rule["view"]),
           "value": None, "period_end": None, "crossed": None, "status": "no_value"}
    if period_end is None:
        point = points[-1] if points else None
    else:
        near = [p for p in points
                if abs((p["period_end"] - period_end).days) <= PERIOD_TOLERANCE_DAYS]
        point = min(near, key=lambda p: abs((p["period_end"] - period_end).days)) if near else None
    if point is None or point["value"] is None:
        return out
    value = Decimal(point["value"])
    return {**out, "value": str(value), "period_end": point["period_end"].isoformat(),
            "crossed": crossed(value, rule["op"], Decimal(rule["threshold"])), "status": "ok"}


def red_line_points(store: Any, user_id: str, company_id: str, rule: dict) -> list[dict]:
    """The rule's formula for one company, through the formula compiler (bound parameters only)
    and Store as the user (RLS). The caller has already passed layer 1 (financials)."""
    ast = parse(rule["formula"])
    a = analyze(ast, rule["view"])
    if a.tickers:
        return []
    compiled = compile_formula(ast, a, {None: company_id}, "fiscal")
    return store.formula(user_id, compiled)


def plain_value(value: str | None, unit: str | None, display: str | None = None) -> str:
    """A figure for a message: $39.3B, 75.0%, $1.25 a share."""
    if value is None:
        return "n/a"
    v = Decimal(value)
    if unit == "ratio" or display == PERCENT:
        return f"{v * 100:.1f}%"
    if unit == "USD/shares":
        return f"${v:,.2f} a share"
    if unit == "USD":
        for size, suffix in ((Decimal(10) ** 12, "T"), (Decimal(10) ** 9, "B"),
                             (Decimal(10) ** 6, "M")):
            if abs(v) >= size:
                return f"${v / size:,.1f}{suffix}"
        return f"${v:,.0f}"
    return f"{v:,.2f}"


def message(n: dict, company: str, links: dict[str, str]) -> str:
    """The text a Discord or Slack channel shows for a notification."""
    article = "an" if n["form"].startswith("8") else "a"
    lines = [f"{company} filed {article} {n['form']} on {n['filed']}"
             + (f" (period ended {n['period_end']})" if n.get("period_end") and n["form"] != "8-K"
                else "")]
    detail = n.get("detail") or {}
    if n["form"] == "8-K":
        items = item_info(n.get("items"))
        lines.append("Items: " + ("; ".join(f"{i['item']} {i['title']}" for i in items)
                                  if items else "not known yet"))
    for f in detail.get("figures") or []:
        line = f"{f['label']}: {plain_value(f['value'], f['unit'])}"
        if f.get("pct_change") is not None:
            line += f" ({Decimal(f['pct_change']) * 100:+.1f}% on a year earlier)"
        elif f.get("change") is not None and f["unit"] == "ratio":
            line += f" ({Decimal(f['change']) * 100:+.1f} pts on a year earlier)"
        lines.append(line)
    for r in detail.get("red_lines") or []:
        if r["status"] == "ok":
            state = "CROSSED" if r["crossed"] else "clear"
            lines.append(f"Red line {r['label']}: {state} "
                         f"({plain_value(r['value'], None, r.get('display'))})")
    lines += [f"{k}: {v}" for k, v in links.items()]
    return "\n".join(lines)


# --- storage ----------------------------------------------------------------------------------

class UnknownUser(Exception):
    """No app.users row for the caller."""


class Pool(Protocol):
    def connection(self) -> Any: ...


NOTIFICATION_COLUMNS = ("notification_id::text, company_id, accession_no, form, filed, period_end,"
                        " items, sources, status, detail, created_at, ready_at, read_at")
WEBHOOK_COLUMNS = "webhook_id::text, url, format, enabled, created_at, rotated_at"
DELIVERY_COLUMNS = ("delivery_id::text, webhook_id::text, notification_id::text, status, attempts,"
                    " next_attempt_at, last_attempt_at, last_status, last_error, created_at,"
                    " delivered_at")
# a failed delivery is tried again after these waits, then given up (the job runs every 10 min)
BACKOFF = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=6))


class AppAlerts:
    """Every method is one transaction as `user_id` (app.user_id for the forced row-level
    security), filtering on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    # follows and settings
    def follows(self, user_id: str) -> list[dict]:
        """company_id, sources, since (the earliest day it was followed, US Eastern)."""
        with self._tx(user_id) as cur:
            cur.execute(
                """
                SELECT company_id, array_agg(DISTINCT source ORDER BY source) AS sources,
                       min((since AT TIME ZONE 'America/New_York')::date) AS since
                FROM (
                    SELECT company_id, 'watchlist' AS source, added_at AS since
                    FROM watchlists WHERE user_id = %(u)s
                    UNION ALL
                    SELECT project->>'companyId', 'analysis', created_at
                    FROM workspaces
                    WHERE user_id = %(u)s AND project IS NOT NULL
                      AND project->>'companyId' ~ '^\\d{10}$'
                ) f
                GROUP BY company_id ORDER BY company_id
                """, {"u": user_id})
            return cur.fetchall()

    def settings(self, user_id: str) -> dict[str, list[str]]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT company_id, forms FROM alert_settings WHERE user_id = %s",
                        (user_id,))
            return {r["company_id"]: r["forms"] for r in cur.fetchall()}

    def put_setting(self, user_id: str, company_id: str, forms: list[str]) -> None:
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT count(*) AS n FROM alert_settings WHERE user_id = %s"
                            " AND company_id <> %s", (user_id, company_id))
                if (cur.fetchone() or {"n": 0})["n"] >= MAX_SETTINGS:
                    raise ValueError(f"at most {MAX_SETTINGS} companies' settings")
                if sorted(forms) == sorted(FORMS):  # the default: no row
                    cur.execute("DELETE FROM alert_settings WHERE user_id = %s AND company_id = %s",
                                (user_id, company_id))
                    return
                cur.execute("INSERT INTO alert_settings (user_id, company_id, forms, updated_at)"
                            " VALUES (%s, %s, %s, statement_timestamp())"
                            " ON CONFLICT (user_id, company_id) DO UPDATE"
                            " SET forms = EXCLUDED.forms, updated_at = EXCLUDED.updated_at",
                            (user_id, company_id, forms))
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    # notifications
    def notifications(self, user_id: str, company_id: str | None, limit: int,
                      before: datetime | None = None) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(
                f"SELECT {NOTIFICATION_COLUMNS} FROM notifications"
                " WHERE user_id = %(u)s AND status <> 'skipped'"
                "   AND (%(c)s::text IS NULL OR company_id = %(c)s)"
                "   AND (%(b)s::timestamptz IS NULL OR created_at < %(b)s)"
                " ORDER BY created_at DESC, filed DESC, notification_id LIMIT %(n)s",
                {"u": user_id, "c": company_id, "b": before, "n": limit})
            return cur.fetchall()

    def unread(self, user_id: str) -> dict:
        with self._tx(user_id) as cur:
            cur.execute("SELECT count(*) FILTER (WHERE read_at IS NULL) AS unread,"
                        "       max(created_at) AS latest"
                        " FROM notifications WHERE user_id = %s AND status <> 'skipped'",
                        (user_id,))
            row = cur.fetchone() or {"unread": 0, "latest": None}
            cur.execute("SELECT checked_at, filings_through FROM alert_checks WHERE user_id = %s",
                        (user_id,))
            return {**row, "last_check": cur.fetchone()}

    def mark_read(self, user_id: str, ids: list[str] | None, company_id: str | None) -> int:
        with self._tx(user_id) as cur:
            cur.execute("UPDATE notifications SET read_at = statement_timestamp()"
                        " WHERE user_id = %(u)s AND read_at IS NULL AND status <> 'skipped'"
                        "   AND (%(ids)s::uuid[] IS NULL OR notification_id = ANY(%(ids)s::uuid[]))"
                        "   AND (%(c)s::text IS NULL OR company_id = %(c)s)",
                        {"u": user_id, "ids": ids, "c": company_id})
            return cur.rowcount

    # red lines
    def red_lines(self, user_id: str, company_id: str | None) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT rule_id::text, company_id, label, formula, view, op, threshold,"
                        "       created_at FROM red_lines"
                        " WHERE user_id = %(u)s AND (%(c)s::text IS NULL OR company_id = %(c)s)"
                        " ORDER BY company_id, created_at, rule_id",
                        {"u": user_id, "c": company_id})
            return cur.fetchall()

    def add_red_line(self, user_id: str, company_id: str, label: str, formula: str, view: str,
                     op: str, threshold: Decimal) -> dict | None:
        """None: the company has MAX_RED_LINES already."""
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("red_lines:" + user_id,))
                cur.execute("SELECT count(*) AS n FROM red_lines WHERE user_id = %s"
                            " AND company_id = %s", (user_id, company_id))
                if (cur.fetchone() or {"n": 0})["n"] >= MAX_RED_LINES:
                    return None
                cur.execute("INSERT INTO red_lines (user_id, company_id, label, formula, view, op,"
                            " threshold) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                            " RETURNING rule_id::text, company_id, label, formula, view, op,"
                            " threshold, created_at",
                            (user_id, company_id, label, formula, view, op, threshold))
                return cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def delete_red_line(self, user_id: str, rule_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM red_lines WHERE user_id = %s AND rule_id = %s",
                        (user_id, rule_id))
            return cur.rowcount == 1

    # webhooks
    def webhooks(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(
                f"SELECT {WEBHOOK_COLUMNS},"
                "  (SELECT jsonb_build_object('status', d.status, 'last_status', d.last_status,"
                "          'last_error', d.last_error, 'at', coalesce(d.last_attempt_at,"
                "          d.created_at)) FROM webhook_deliveries d"
                "   WHERE d.user_id = w.user_id AND d.webhook_id = w.webhook_id"
                "   ORDER BY d.created_at DESC LIMIT 1) AS last_delivery"
                " FROM webhooks w WHERE w.user_id = %s ORDER BY w.created_at, w.webhook_id",
                (user_id,))
            return cur.fetchall()

    def webhook(self, user_id: str, webhook_id: str) -> dict | None:
        """With its salt: for signing only, never for a response."""
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {WEBHOOK_COLUMNS}, secret_salt FROM webhooks"
                        " WHERE user_id = %s AND webhook_id = %s", (user_id, webhook_id))
            return cur.fetchone()

    def add_webhook(self, user_id: str, url: str, fmt: str, salt: bytes) -> dict | None:
        """None: the user has MAX_WEBHOOKS already."""
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("webhooks:" + user_id,))
                cur.execute("SELECT count(*) AS n FROM webhooks WHERE user_id = %s", (user_id,))
                if (cur.fetchone() or {"n": 0})["n"] >= MAX_WEBHOOKS:
                    return None
                cur.execute("INSERT INTO webhooks (user_id, url, format, secret_salt)"
                            f" VALUES (%s, %s, %s, %s) RETURNING {WEBHOOK_COLUMNS}",
                            (user_id, url, fmt, salt))
                return cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def set_webhook(self, user_id: str, webhook_id: str, enabled: bool) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute(f"UPDATE webhooks SET enabled = %s WHERE user_id = %s AND webhook_id = %s"
                        f" RETURNING {WEBHOOK_COLUMNS}", (enabled, user_id, webhook_id))
            return cur.fetchone()

    def rotate_webhook(self, user_id: str, webhook_id: str, salt: bytes) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute("UPDATE webhooks SET secret_salt = %s, rotated_at = statement_timestamp()"
                        f" WHERE user_id = %s AND webhook_id = %s RETURNING {WEBHOOK_COLUMNS}",
                        (salt, user_id, webhook_id))
            return cur.fetchone()

    def delete_webhook(self, user_id: str, webhook_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM webhooks WHERE user_id = %s AND webhook_id = %s",
                        (user_id, webhook_id))
            return cur.rowcount == 1

    def record_test(self, user_id: str, webhook_id: str, sent: Sent, at: datetime) -> dict | None:
        """A test send, already made: one finished row (never retried)."""
        with self._tx(user_id) as cur:
            cur.execute(
                "INSERT INTO webhook_deliveries (user_id, webhook_id, status, attempts,"
                " next_attempt_at, last_attempt_at, last_status, last_error, delivered_at)"
                " SELECT %(u)s, w.webhook_id, %(s)s, 1, %(at)s, %(at)s, %(st)s, %(e)s,"
                "        CASE WHEN %(ok)s THEN %(at)s END"
                " FROM webhooks w WHERE w.user_id = %(u)s AND w.webhook_id = %(w)s"
                f" RETURNING {DELIVERY_COLUMNS}",
                {"u": user_id, "w": webhook_id, "s": "delivered" if sent.ok else "failed",
                 "at": at, "st": sent.status, "e": sent.error, "ok": sent.ok})
            return cur.fetchone()

    def deliveries(self, user_id: str, webhook_id: str | None, limit: int) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {DELIVERY_COLUMNS} FROM webhook_deliveries"
                        " WHERE user_id = %(u)s AND (%(w)s::uuid IS NULL OR webhook_id = %(w)s)"
                        " ORDER BY created_at DESC, delivery_id LIMIT %(n)s",
                        {"u": user_id, "w": webhook_id, "n": limit})
            return cur.fetchall()

    # the job (query_api/alert_job.py)
    def users(self) -> list[str]:
        """Every user (app.users has no row-level security: it's the CDC source of access)."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users ORDER BY user_id")
            return [r[0] for r in cur.fetchall()]

    def plan(self, user_id: str) -> dict:
        """What the job needs for a user, in one transaction: follows, settings, red lines,
        whether a webhook is on, pending notifications."""
        follows = self.follows(user_id)
        with self._tx(user_id) as cur:
            cur.execute("SELECT company_id, forms FROM alert_settings WHERE user_id = %s",
                        (user_id,))
            settings = {r["company_id"]: r["forms"] for r in cur.fetchall()}
            cur.execute("SELECT rule_id::text, company_id, label, formula, view, op, threshold"
                        " FROM red_lines WHERE user_id = %s ORDER BY created_at, rule_id",
                        (user_id,))
            rules: dict[str, list[dict]] = {}
            for r in cur.fetchall():
                rules.setdefault(r["company_id"], []).append(r)
            cur.execute("SELECT count(*) AS n FROM webhooks WHERE user_id = %s AND enabled",
                        (user_id,))
            hooks = (cur.fetchone() or {"n": 0})["n"]
        return {"follows": follows, "settings": settings, "red_lines": rules, "webhooks": hooks}

    def add_notifications(self, user_id: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        with self._tx(user_id) as cur:
            n = 0
            for r in rows:
                cur.execute("INSERT INTO notifications (user_id, company_id, accession_no, form,"
                            " filed, period_end, items, sources, detail)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                            " ON CONFLICT (user_id, accession_no, company_id) DO NOTHING",
                            (user_id, r["company_id"], r["accession_no"], r["form"], r["filed"],
                             r["period_end"], r["items"], r["sources"],
                             Jsonb({"cik": r["cik"]} if r.get("cik") else {})))
                n += cur.rowcount
            return n

    def pending(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {NOTIFICATION_COLUMNS} FROM notifications"
                        " WHERE user_id = %s AND status = 'pending'"
                        " ORDER BY created_at, notification_id", (user_id,))
            return cur.fetchall()

    def finish(self, user_id: str, updates: list[dict], at: datetime) -> list[str]:
        """Writes each update (status, items, detail); a notification that became ready gets a
        delivery for every enabled webhook. Returns the ids that became ready."""
        ready: list[str] = []
        if not updates:
            return ready
        with self._tx(user_id) as cur:
            for u in updates:
                cur.execute("UPDATE notifications SET status = %s, items = %s, detail = %s,"
                            " ready_at = CASE WHEN %s = 'ready' THEN %s END"
                            " WHERE user_id = %s AND notification_id = %s AND status = 'pending'",
                            (u["status"], u["items"], Jsonb(u["detail"]), u["status"], at,
                             user_id, u["notification_id"]))
                if cur.rowcount and u["status"] == "ready":
                    ready.append(u["notification_id"])
            if ready:
                cur.execute("INSERT INTO webhook_deliveries (user_id, webhook_id, notification_id,"
                            " next_attempt_at)"
                            " SELECT %(u)s, w.webhook_id, n.notification_id, %(at)s"
                            " FROM webhooks w CROSS JOIN unnest(%(ids)s::uuid[]) n(notification_id)"
                            " WHERE w.user_id = %(u)s AND w.enabled"
                            " ON CONFLICT (webhook_id, notification_id) DO NOTHING",
                            {"u": user_id, "at": at, "ids": ready})
        return ready

    def due(self, user_id: str, at: datetime, limit: int = 50) -> list[dict]:
        """Deliveries to make now, with their webhook (URL, format, salt) and notification."""
        with self._tx(user_id) as cur:
            cur.execute(
                "SELECT d.delivery_id::text, d.attempts, w.webhook_id::text, w.url, w.format,"
                "       w.secret_salt, n.notification_id::text, n.company_id, n.accession_no,"
                "       n.form, n.filed, n.period_end, n.items, n.sources, n.detail"
                " FROM webhook_deliveries d"
                " JOIN webhooks w ON w.webhook_id = d.webhook_id AND w.user_id = d.user_id"
                " JOIN notifications n ON n.notification_id = d.notification_id"
                "   AND n.user_id = d.user_id"
                " WHERE d.user_id = %s AND d.status = 'pending' AND d.next_attempt_at <= %s"
                "   AND w.enabled"
                " ORDER BY d.next_attempt_at, d.delivery_id LIMIT %s", (user_id, at, limit))
            return cur.fetchall()

    def record(self, user_id: str, results: list[tuple[str, int, Sent]], at: datetime) -> None:
        """Each (delivery, attempts before, result): delivered, or tried again after BACKOFF,
        or failed for good after the last wait."""
        if not results:
            return
        with self._tx(user_id) as cur:
            for delivery_id, attempts, sent in results:
                tries = attempts + 1
                if sent.ok:
                    status, nxt = "delivered", at
                elif tries > len(BACKOFF):
                    status, nxt = "failed", at
                else:
                    status, nxt = "pending", at + BACKOFF[tries - 1]
                cur.execute("UPDATE webhook_deliveries SET status = %s, attempts = %s,"
                            " next_attempt_at = %s, last_attempt_at = %s, last_status = %s,"
                            " last_error = %s, delivered_at = CASE WHEN %s THEN %s END"
                            " WHERE user_id = %s AND delivery_id = %s",
                            (status, tries, nxt, at, sent.status, sent.error, sent.ok, at,
                             user_id, delivery_id))

    def checked(self, user_id: str, at: datetime, through: date | None) -> None:
        with self._tx(user_id) as cur:
            cur.execute("INSERT INTO alert_checks (user_id, checked_at, filings_through)"
                        " VALUES (%s, %s, %s) ON CONFLICT (user_id) DO UPDATE"
                        " SET checked_at = EXCLUDED.checked_at,"
                        "     filings_through = coalesce(EXCLUDED.filings_through,"
                        "                                alert_checks.filings_through)",
                        (user_id, at, through))

    def prune(self, user_id: str, before: datetime) -> int:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM notifications WHERE user_id = %s AND created_at < %s",
                        (user_id, before))
            return cur.rowcount


# --- shapes -----------------------------------------------------------------------------------

def notification_out(n: dict, can_read_financials: bool) -> dict:
    """A notification as the API returns it: figures and red-line values only while the user
    can read the company's financials (module docstring)."""
    detail = n.get("detail") or {}
    figures_status = detail.get("figures_status") or ("pending" if n["status"] == "pending"
                                                      else None)
    figures = detail.get("figures")
    red_lines = detail.get("red_lines") or []
    if not can_read_financials and (figures or red_lines):
        figures, red_lines, figures_status = None, [], "no_access"
    return {
        "id": n["notification_id"], "company_id": n["company_id"],
        "accession_no": n["accession_no"], "form": n["form"], "filed": n["filed"],
        "filing_url": filing_url(detail.get("cik") or n["company_id"], n["accession_no"]),
        "period_end": n["period_end"], "items": item_info(n["items"]),
        "items_known": n["items"] is not None, "sources": n["sources"], "status": n["status"],
        "figures": figures, "figures_status": figures_status, "red_lines": red_lines,
        "crossed": sum(1 for r in red_lines if r.get("crossed")),
        "created_at": n["created_at"], "ready_at": n["ready_at"], "read_at": n["read_at"],
    }


def webhook_out(w: dict) -> dict:
    return {"id": w["webhook_id"], "url": mask(w["url"]), "format": w["format"],
            "enabled": w["enabled"], "created_at": w["created_at"],
            "rotated_at": w.get("rotated_at"), "last_delivery": w.get("last_delivery")}


def delivery_out(d: dict) -> dict:
    return {"id": d["delivery_id"], "webhook_id": d["webhook_id"],
            "notification_id": d["notification_id"], "test": d["notification_id"] is None,
            "status": d["status"], "attempts": d["attempts"],
            "next_attempt_at": d["next_attempt_at"] if d["status"] == "pending" else None,
            "last_attempt_at": d["last_attempt_at"], "last_status": d["last_status"],
            "last_error": d["last_error"], "created_at": d["created_at"],
            "delivered_at": d["delivered_at"]}


def test_event(at: datetime) -> dict:
    return {"type": "test", "sent_at": at.isoformat(),
            "message": "A test from Tributary: alerts for your companies will arrive here."}


# --- routes -----------------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingBody(_Strict):
    forms: list[Literal["10-K", "10-Q", "8-K"]] = Field(max_length=3)

    @field_validator("forms")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        return [f for f in FORMS if f in v]


class ReadBody(_Strict):
    ids: list[Annotated[str, Field(pattern=UUID_RE.pattern)]] | None = Field(None, max_length=200)
    company_id: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    all: bool = False


class RedLineBody(_Strict):
    company_id: Annotated[str, Field(pattern=r"^\d{10}$")]
    label: Annotated[str, Field(min_length=1, max_length=120)]
    formula: Annotated[str, Field(min_length=1, max_length=500)]
    view: Literal["quarter", "ttm"] = "ttm"
    op: Literal["<", "<=", ">", ">="]
    threshold: Annotated[str, Field(max_length=40)]

    @field_validator("threshold")
    @classmethod
    def _number(cls, v: str) -> str:
        try:
            d = Decimal(v.strip())
        except InvalidOperation:
            raise ValueError("a number, e.g. 0.55") from None
        if not d.is_finite() or abs(d) >= Decimal(10) ** 15:
            raise ValueError("a number, e.g. 0.55")
        return str(d)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("a short name for the red line")
        return v


class WebhookPatch(_Strict):
    enabled: bool


def _bad(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def add_alert_routes(app: FastAPI, *, alerts: AppAlerts | None, store: Any,
                     current_user: Callable[..., str],
                     require: Callable[[str, str, str], None],
                     readable: Callable[[str], Readable],
                     jwt_secret: str,
                     sender: Sender | None = None,
                     resolver: Callable[..., Any] | None = None,
                     clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                     test_sends: Limiter | None = None) -> None:
    """The /v1/alerts routes. Without the app database every one answers 503 (the web app hides
    the bell on a 404 from an older API and shows "unavailable" on a 503)."""
    User = Annotated[str, Depends(current_user)]
    send = sender or HttpsSender()
    limiter = test_sends or Limiter(TEST_SENDS_PER_HOUR)

    def db() -> AppAlerts:
        if alerts is None:
            raise HTTPException(503, "alerts unavailable")
        return alerts

    def financials_readable(user_id: str) -> Callable[[str], bool]:
        access = readable(user_id)
        return lambda c: "financials" in access.get(c, set())

    @app.get("/v1/alerts/unread", response_model=dict)
    def unread(user_id: User) -> dict:
        """The bell: unread notifications, the newest one's time and the job's last look."""
        r = db().unread(user_id)
        return {"unread": r["unread"], "latest": r["latest"], "last_check": r["last_check"]}

    @app.get("/v1/alerts/notifications", response_model=dict)
    def notifications(
        user_id: User,
        company_id: Annotated[str | None, Query(pattern=r"^\d{10}$")] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 50,
        before: Annotated[datetime | None, Query(description="created before (paging)")] = None,
    ) -> dict:
        """The caller's notifications, newest first (pending ones too: their figures or items
        aren't in yet). Figures only for companies whose financials the caller reads now."""
        rows = db().notifications(user_id, company_id, limit + 1, before)
        can = financials_readable(user_id) if rows else (lambda _c: False)
        return {"notifications": [notification_out(n, can(n["company_id"]))
                                  for n in rows[:limit]],
                "has_more": len(rows) > limit, "lookback_days": LOOKBACK_DAYS,
                "pending_days": PENDING_DAYS}

    @app.post("/v1/alerts/notifications/read", response_model=dict)
    def read(user_id: User, body: ReadBody) -> dict:
        """Marks the caller's notifications read: these ids, one company's, or all."""
        if not body.all and body.ids is None and body.company_id is None:
            raise HTTPException(422, "give ids, a company_id or all: true")
        n = db().mark_read(user_id, None if body.all else body.ids, body.company_id)
        return {"marked": n}

    @app.get("/v1/alerts/settings", response_model=dict)
    def settings(user_id: User) -> dict:
        """Followed companies (watchlist, analysis projects) with the forms alerted, the
        caller's webhooks (URLs masked, never a secret) and the job's last look."""
        a = db()
        follows = a.follows(user_id)
        chosen = a.settings(user_id)
        status = a.unread(user_id)
        return {"forms": list(FORMS),
                "companies": [{"company_id": f["company_id"], "sources": f["sources"],
                               "since": f["since"],
                               "forms": chosen.get(f["company_id"], list(FORMS))}
                              for f in follows],
                "webhooks": [webhook_out(w) for w in a.webhooks(user_id)],
                "formats": list(FORMATS), "max_webhooks": MAX_WEBHOOKS,
                "email": {"available": False},
                "last_check": status["last_check"],
                "schedule": "every 10 minutes", "lookback_days": LOOKBACK_DAYS,
                "pending_days": PENDING_DAYS}

    @app.put("/v1/alerts/settings/{company_id}", response_model=dict)
    def put_setting(user_id: User, company_id: CompanyId, body: SettingBody) -> dict:
        """The forms alerted for one company ([] = none)."""
        try:
            db().put_setting(user_id, company_id, body.forms)
        except UnknownUser:
            raise HTTPException(409, "no account to save settings to") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return {"company_id": company_id, "forms": body.forms}

    @app.get("/v1/alerts/red-lines", response_model=dict)
    def red_lines(
        user_id: User,
        company_id: Annotated[str | None, Query(pattern=r"^\d{10}$")] = None,
        evaluate_now: Annotated[bool, Query(
            alias="evaluate", description="with company_id: each red line on the company's "
                                          "latest figures (needs financials)")] = False,
    ) -> dict:
        rules = db().red_lines(user_id, company_id)
        out: dict[str, Any] = {"red_lines": [
            {**r, "threshold": str(r["threshold"]),
             "display": display_of(r["formula"], r["view"])} for r in rules],
            "max_per_company": MAX_RED_LINES}
        if evaluate_now:
            if company_id is None:
                raise HTTPException(422, "evaluate needs a company_id")
            require(user_id, "financials", company_id)
            out["evaluation"] = [evaluate(r, red_line_points(store, user_id, company_id, r), None)
                                 for r in rules]
            out["evaluated_at"] = clock()
        return out

    @app.post("/v1/alerts/red-lines", response_model=dict)
    def add_red_line(user_id: User, body: RedLineBody) -> dict:
        """A red line: checked like a chart formula (no ticker), saved for the company."""
        problem = check_formula(body.formula, body.view)
        if problem:
            raise HTTPException(422, f"formula: {problem}")
        try:
            row = db().add_red_line(user_id, body.company_id, body.label, body.formula.strip(),
                                    body.view, body.op, Decimal(body.threshold))
        except UnknownUser:
            raise HTTPException(409, "no account to save red lines to") from None
        if row is None:
            raise HTTPException(409, f"a company has at most {MAX_RED_LINES} red lines")
        return {"red_line": {**row, "threshold": str(row["threshold"]),
                             "display": display_of(row["formula"], row["view"])}}

    @app.delete("/v1/alerts/red-lines/{rule_id}", response_model=dict)
    def delete_red_line(user_id: User, rule_id: Id) -> dict:
        if not db().delete_red_line(user_id, rule_id):
            raise HTTPException(404, "no such red line")
        return {"deleted": True}

    @app.get("/v1/alerts/webhooks", response_model=dict)
    def webhooks(user_id: User) -> dict:
        return {"webhooks": [webhook_out(w) for w in db().webhooks(user_id)]}

    async def json_body(request: Request) -> dict | JSONResponse:
        size = request.headers.get("content-length")
        if size is None or not size.isdigit() or int(size) > MAX_BODY:
            return _bad(413, "too_large", f"send a JSON body of at most {MAX_BODY} bytes")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return _bad(413, "too_large", f"send a JSON body of at most {MAX_BODY} bytes")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return _bad(422, "invalid", 'send {"url": "https://...", "format": "json"}')
        if not isinstance(body, dict):
            return _bad(422, "invalid", 'send {"url": "https://...", "format": "json"}')
        return body

    @app.post("/v1/alerts/webhooks")
    async def add_webhook(request: Request, user_id: User):
        """{"url", "format"?}: refused unless https to a public address (query_api.webhooks),
        checked now and before every send. The answer carries the signing secret, this once.
        The body is parsed by hand: FastAPI's 422 would echo the URL, which carries a token."""
        body = await json_body(request)
        if isinstance(body, JSONResponse):
            return body
        url, fmt = body.get("url"), body.get("format")
        if set(body) - {"url", "format"} or not isinstance(url, str) \
                or (fmt is not None and fmt not in FORMATS):
            return _bad(422, "invalid", 'send {"url": "https://...", "format": "json", "discord"'
                                        ' or "slack"}')
        url = url.strip()
        try:
            await run_in_threadpool(vet, url, *((resolver,) if resolver else ()))
        except WebhookRefused as e:
            return _bad(422, e.code, str(e))
        salt = new_salt()
        try:
            row = await run_in_threadpool(db().add_webhook, user_id, url,
                                          fmt or suggest_format(url), salt)
        except UnknownUser:
            return _bad(409, "unknown_user", "no account to save a webhook to")
        if row is None:
            return _bad(409, "limit", f"at most {MAX_WEBHOOKS} webhooks")
        log.info("webhook added", extra={"user_id": user_id, "webhook_id": row["webhook_id"]})
        return {"webhook": webhook_out(row),
                "secret": webhook_secret(jwt_secret, row["webhook_id"], salt),
                "secret_shown_once": True}

    @app.patch("/v1/alerts/webhooks/{webhook_id}", response_model=dict)
    def patch_webhook(user_id: User, webhook_id: Id, body: WebhookPatch) -> dict:
        row = db().set_webhook(user_id, webhook_id, body.enabled)
        if row is None:
            raise HTTPException(404, "no such webhook")
        return {"webhook": webhook_out(row)}

    @app.post("/v1/alerts/webhooks/{webhook_id}/rotate", response_model=dict)
    def rotate_webhook(user_id: User, webhook_id: Id) -> dict:
        """A new signing secret, shown this once; the old one stops working."""
        salt = new_salt()
        row = db().rotate_webhook(user_id, webhook_id, salt)
        if row is None:
            raise HTTPException(404, "no such webhook")
        return {"webhook": webhook_out(row),
                "secret": webhook_secret(jwt_secret, row["webhook_id"], salt),
                "secret_shown_once": True}

    @app.delete("/v1/alerts/webhooks/{webhook_id}", response_model=dict)
    def delete_webhook(user_id: User, webhook_id: Id) -> dict:
        if not db().delete_webhook(user_id, webhook_id):
            raise HTTPException(404, "no such webhook")
        return {"deleted": True}

    @app.post("/v1/alerts/webhooks/{webhook_id}/test", response_model=dict)
    def test_webhook(user_id: User, webhook_id: Id) -> dict:
        """Sends a signed test event to the caller's own webhook now (TEST_SENDS_PER_HOUR),
        through the same checks as an alert; the result goes in the delivery log."""
        a = db()
        hook = a.webhook(user_id, webhook_id)
        if hook is None:
            raise HTTPException(404, "no such webhook")
        if not limiter.take(user_id):
            raise HTTPException(429, f"at most {TEST_SENDS_PER_HOUR} test sends an hour")
        at = clock()
        secret = webhook_secret(jwt_secret, hook["webhook_id"], bytes(hook["secret_salt"]))
        body = body_of(hook["format"], test_event(at),
                       "A test from Tributary: alerts for your companies will arrive here.")
        sent = send.send(hook["url"], body, headers(secret, "test", "test", body))
        row = a.record_test(user_id, webhook_id, sent, at)
        return {"delivery": delivery_out(row) if row else None, "ok": sent.ok}

    @app.get("/v1/alerts/deliveries", response_model=dict)
    def deliveries(
        user_id: User,
        webhook_id: Annotated[str | None, Query(pattern=UUID_RE.pattern)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 50,
    ) -> dict:
        """The caller's delivery log, newest first: status, attempts, the last HTTP status and
        a short error code (never a URL or a response body)."""
        return {"deliveries": [delivery_out(d)
                               for d in db().deliveries(user_id, webhook_id, limit)],
                "retries": [int(b.total_seconds()) for b in BACKOFF]}
