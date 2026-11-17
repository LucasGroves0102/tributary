"""Saved screens that alert (Direction A, app migration 024): a user watches one of their saved
screens (a workspace item of kind `screen`) and hears when a company enters or leaves it, or a
column's value crosses a line, after the nightly figures land: in the app (the bell, the Alerts
page), at their webhooks and, once email is on, by email, like a filing alert (alerts.py). The
alerts job (alert_job.py, every ten minutes) evaluates the watches (watch_job.py); this module
holds the rules, the app-database storage and the routes.

Rules (the spec; README "Saved screens that alert"; test_watches.py):

- A watch is on one of the user's saved screens: "companies entering", "companies leaving" and
  up to MAX_LINES lines (a column rises above or falls below a value), each for one named
  company or any company in the screen. One watch a screen, MAX_WATCHES a user. `source` is
  'screen'; a saved SQL query is meant to be another source (B3b): a source only has to give
  `Snapshot` (company ids in it and the lines' values), the rest of this module is the same.
- When: a watch is due once the nightly figures have landed since its last check
  (Store.figures_landed_at, tributary 110), or FALLBACK after it if they never do; a new or
  changed watch, or a screen saved over since, at the next run.
- As whom: its owner, never with another user's access. OpenFGA first (the owner's Access: the
  id list to narrow to, none with the catalog open), then Store as the owner (RLS). The screen
  runs `granted_only`, so only companies the owner can read financials for are in it, and layer 1
  drops any row it doesn't allow again. Own metrics resolve through the owner's items and data.
- The screen: what Companies lists for the saved filters (`screen_of`: the web's screenParams,
  then /v1/screener's Screen) with the columns it was shown with when the watch was saved, every
  page of it. More than MAX_MEMBERS companies is `too_broad`: nothing stored, nothing alerted.
- Readings: a line compares the screener's comparable value (`comparable`, k<i>'s rule: the value
  when it reads as current or just behind; nothing for n/m, n/a, old, another currency), so a value
  that doesn't read as one never crosses. A crossing is a company whose readable value was on the
  near side at the last check and is past the line now (above: >, below: <); an "any company"
  line counts only companies in the screen now.
- First check silent: a watch never checked, or whose `basis` changed (the screen's filters, the
  watch's columns or lines, its own metrics' formulas, the owner's access), records what it sees
  and alerts nothing. Only company ids are stored (`members`, `near`), never values.
- Caps: one alert per watch per check, at most MAX_LISTED companies listed across its sections
  (lines first, then entering, then leaving), each with how many more it didn't list.
- Shown and sent: a company the user can no longer read financials for is left out of an alert's
  lists (`hidden` counts them), when it is listed and again when it is sent.

Owner-only: app migration 024's tables have FORCEd row-level security on app.user_id; every
statement here also filters on the user (the JWT `sub` in the routes, the user worked for in the
job). Another user's watch or screen and one that doesn't exist get the same 404. Account routes:
never in api_keys.KEY_ROUTES, no agent or MCP tool.
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Protocol

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from query_api.alerts import UnknownUser, plain_value
from query_api.authorizer import Access, Readable
from query_api.formula.check import DISPLAY, MULTIPLE
from query_api.screener import (
    COLUMNS,
    Filter,
    MetricColumn,
    Screen,
    band_filters,
    metric_key,
)
from query_api.views import DEFAULT_SCREENER, MAX_SCREENER_COLUMNS

log = logging.getLogger("query_api.watches")

SOURCES = ("screen",)
MAX_WATCHES = 20       # per user
MAX_LINES = 5          # per watch
MAX_MEMBERS = 500      # companies a watched screen may hold (the screener's JSON page cap)
MAX_LISTED = 20        # companies one alert lists, then "and N more"
MAX_LIST = 100
LABEL = 60
FALLBACK = timedelta(hours=26)  # a check a day when the nightly figures don't say they landed
SCHEDULE = "after the nightly figures land (about 6 a.m. New York time)"
UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
ItemId = Annotated[str, Path(pattern=UUID)]
_METRIC_KEY = re.compile(r"^m_[0-9a-f]{32}$")


# --- rules ------------------------------------------------------------------------------------

def comparable(value: Any, status: str | None) -> Decimal | None:
    """What a value is compared by: screener k<i>'s rule (screener._values), the value only when
    its reading is current (no status) or just behind the company's latest period; None for n/m,
    n/a, old (stale) and another currency, and for no value."""
    if value is None or status not in (None, "behind"):
        return None
    return Decimal(value)


def past(value: Decimal, line: dict) -> bool:
    """Whether a value is past the line (above: strictly greater; below: strictly less)."""
    t = Decimal(line["threshold"])
    return value > t if line["direction"] == "above" else value < t


def fraction(percent: float) -> Decimal:
    """Percent → fraction as the web app sends it (companies/screen.ts `fraction`:
    Number((percent / 100).toPrecision(12)))."""
    return Decimal(format(percent / 100, ".12g"))


def screen_of(state: dict, columns: list[str], metrics: dict[str, MetricColumn],
              extra: list[str], limit: int = MAX_MEMBERS + 1) -> Screen:
    """The Screen /v1/screener runs for a saved screen's state (workspaces.ScreenState) as the
    Companies page sends it (web companies/screen.ts screenParams: growth and margin as minimum
    fractions, band as revenue_band, one sic, sector, exchange and filer), with these columns
    (the user's, else the default) and the state's own metrics, plus `extra` columns read
    without changing which companies are listed (Screen.blank_columns). Always only companies
    the caller can read (`granted_only`), every page of it in company-id order."""
    filters: list[Filter] = []
    if state.get("growth") is not None:
        filters.append(Filter("revenue_growth_ttm", "gte", fraction(state["growth"])))
    if state.get("margin") is not None:
        filters.append(Filter("operating_margin_ttm", "gte", fraction(state["margin"])))
    if state.get("band"):
        filters += band_filters("revenue_ttm", state["band"])
    shown = list(columns) if columns else list(DEFAULT_SCREENER)
    blank = [*shown, *(k for k in metrics if k not in shown)]
    read = [*blank, *(k for k in dict.fromkeys(extra) if k not in blank)]
    q, issue = state.get("q"), state.get("issue")
    one = lambda key: [state[key]] if state.get(key) else []  # noqa: E731
    return Screen(columns=read, blank_columns=blank if read != blank else None, metrics=metrics,
                  coverage=True, hide_blank=q is None and issue is None, granted_only=True,
                  sic=one("sic"), industry=state.get("industry"), q=q,
                  exchanges=one("exchange"), fye_months=one("fye"),
                  filer_categories=one("filer"), sectors=one("sector"),
                  sector_industries=one("sector_industry"), issue=issue, filters=filters,
                  sort="company_id", limit=limit, offset=0)


def metric_ids(state: dict) -> list[str]:
    """The own metrics (item ids) a saved screen shows as columns."""
    return list(dict.fromkeys(state.get("metric") or []))


def access_key(access: Access) -> dict:
    """What of the owner's access the basis keeps: whether the catalog is open at financials,
    and the companies they read individually at it."""
    return {"public": "financials" in access.public,
            "listed": sorted(c for c, t in access.listed.items() if "financials" in t)}


def basis(state: dict, columns: list[str], lines: list[dict],
          metrics: dict[str, MetricColumn], access: Access) -> str:
    """A hash of everything that decides what the watch sees: when it changes, the next check
    only records (module docstring, "First check silent")."""
    payload = {"state": state, "columns": columns, "lines": lines,
               "metrics": {k: [m.formula, m.view] for k, m in sorted(metrics.items())},
               "access": access_key(access)}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Snapshot:
    """What a source shows its owner now: the company ids in it (readable ones only, sorted), how
    many there are, and per line company_id → (comparable value, exact value)."""
    members: list[str]
    total: int
    values: list[dict[str, tuple[Decimal | None, Any]]] = field(default_factory=list)
    units: list[tuple[str | None, str | None]] = field(default_factory=list)  # (unit, display)

    @property
    def too_broad(self) -> bool:
        return self.total > MAX_MEMBERS


def near_sets(lines: list[dict], snap: Snapshot) -> dict[str, list[str]]:
    """Per line (by position): the companies with a readable value not past it."""
    return {str(i): sorted(c for c, (k, _) in snap.values[i].items()
                           if k is not None and not past(k, line))
            for i, line in enumerate(lines)}


def changes(watch: dict, before: dict | None, snap: Snapshot,
            limit: int = MAX_LISTED) -> tuple[dict | None, dict]:
    """(the alert's detail, or None when there is nothing to say; the state to store). `before`
    is the last check's {members, near}, None when it only records (first check, new basis)."""
    lines = watch.get("lines") or []
    state = {"members": snap.members, "near": near_sets(lines, snap)}
    if before is None:
        return None, state
    prev, now = set(before.get("members") or []), set(snap.members)
    budget = limit
    detail: dict[str, Any] = {}

    def take(ids: list) -> tuple[list, int]:
        nonlocal budget
        shown = ids[:budget]
        budget -= len(shown)
        return shown, len(ids) - len(shown)

    crossed = []
    for i, line in enumerate(lines):
        was = set((before.get("near") or {}).get(str(i)) or [])
        vals = snap.values[i]
        hit = sorted(c for c in was if c in vals and vals[c][0] is not None
                     and past(vals[c][0], line))
        if not hit:
            continue
        shown, more = take(hit)
        unit, display = snap.units[i]
        crossed.append({"line": i, "column": line["column"], "label": line["label"],
                        "direction": line["direction"], "threshold": line["threshold"],
                        "company_id": line.get("company_id"), "unit": unit, "display": display,
                        "companies": [{"company_id": c, "value": str(vals[c][1])} for c in shown],
                        "more": more})
    if crossed:
        detail["crossed"] = crossed
    for key, on, ids in (("entered", watch.get("entering"), sorted(now - prev)),
                         ("left", watch.get("leaving"), sorted(prev - now))):
        if on and ids:
            shown, more = take(ids)
            detail[key] = {"companies": shown, "more": more}
    return (detail or None), state


def listed(detail: dict) -> list[str]:
    """Every company an alert lists, in order, once."""
    out = [c["company_id"] for x in detail.get("crossed") or [] for c in x["companies"]]
    out += [c for k in ("entered", "left") for c in (detail.get(k) or {}).get("companies") or []]
    return list(dict.fromkeys(out))


def visible(detail: dict, can: Callable[[str], bool]) -> dict:
    """The detail without companies the user can no longer read financials for; `hidden`: how
    many it left out (module docstring, "Shown and sent")."""
    hidden = 0
    out: dict[str, Any] = {k: v for k, v in detail.items()
                           if k not in ("crossed", "entered", "left")}
    keep = {c: can(c) for c in listed(detail)}
    crossed = []
    for x in detail.get("crossed") or []:
        rows = [c for c in x["companies"] if keep[c["company_id"]]]
        hidden += len(x["companies"]) - len(rows)
        if rows or x.get("more"):
            crossed.append({**x, "companies": rows})
    if crossed:
        out["crossed"] = crossed
    for k in ("entered", "left"):
        if k in detail:
            ids = [c for c in detail[k]["companies"] if keep[c]]
            hidden += len(detail[k]["companies"]) - len(ids)
            if ids or detail[k].get("more"):
                out[k] = {**detail[k], "companies": ids}
    out["hidden"] = hidden
    return out


def unit_of(column: str, metrics: dict[str, MetricColumn]) -> tuple[str | None, str | None]:
    """(unit, display) a column's values read in: an own metric's, else the catalog's."""
    if column in metrics:
        return metrics[column].unit, metrics[column].display
    if column in COLUMNS:
        metric, _, unit = COLUMNS[column]
        return unit, DISPLAY.get(metric)
    return None, None


def figure(value: str | None, unit: str | None, display: str | None) -> str:
    """A value or threshold in words: 26.1%, 1.50x, $1.2B (alerts.plain_value)."""
    if value is None:
        return "n/a"
    if display == MULTIPLE:
        return f"{Decimal(value):,.2f}x"
    if display == "number":
        return f"{Decimal(value):,.2f}"
    return plain_value(value, unit, display)


def default_label(column: str) -> str:
    """A plain column's name when the web app sends none: operating_margin_ttm → Operating margin
    (TTM)."""
    if column not in COLUMNS:
        return "Your metric"
    metric, span, _ = COLUMNS[column]
    return f"{metric.replace('_', ' ').capitalize()} ({'TTM' if span == 'ttm' else 'quarter'})"


def line_words(line: dict) -> str:
    """'Operating margin (TTM) above 25.0%'."""
    unit, display = line.get("unit"), line.get("display")
    return (f"{line['label']} {'above' if line['direction'] == 'above' else 'below'} "
            f"{figure(line['threshold'], unit, display)}")


def screen_links(app_url: str, item_id: str) -> dict[str, str]:
    return {"Open the screen": f"{app_url}/#/screen/{item_id}"}


def message(alert: dict, label: Callable[[str], str], links: dict[str, str]) -> str:
    """The text a Discord or Slack channel (and an email) shows for a screen alert."""
    d = alert["detail"]
    lines = [f"Your screen “{alert['name']}” changed with the latest figures"]
    for x in d.get("crossed") or []:
        verb = "rose above" if x["direction"] == "above" else "fell below"
        unit, display = x.get("unit"), x.get("display")
        names = "; ".join(f"{label(c['company_id'])} {figure(c['value'], unit, display)}"
                          for c in x["companies"])
        lines.append(f"{x['label']} {verb} {figure(x['threshold'], unit, display)}: {names}"
                     + (f" and {x['more']} more" if x.get("more") else ""))
    for key, verb in (("entered", "Entered"), ("left", "Left")):
        if key in d:
            names = ", ".join(label(c) for c in d[key]["companies"])
            more = d[key].get("more") or 0
            lines.append(f"{verb}: {names}" + (f" and {more} more" if more else ""))
    if d.get("hidden"):
        lines.append(f"{d['hidden']} more you can no longer read")
    lines += [f"{k}: {v}" for k, v in links.items()]
    return "\n".join(lines)


def event(alert: dict, company: Callable[[str], dict], links: dict[str, str], app_url: str,
          at: datetime) -> dict:
    """The JSON a webhook of format `json` receives (X-Tributary-Event: screen)."""
    d = alert["detail"]

    def co(cid: str) -> dict:
        return {**company(cid), "url": f"{app_url}/#/company/{cid}"}

    return {"type": "screen", "id": alert["screen_alert_id"], "sent_at": at.isoformat(),
            "screen": {"item_id": alert["screen_item_id"], "name": alert["name"]},
            "figures_at": d.get("figures_at"),
            "crossed": [{"label": x["label"], "column": x["column"], "direction": x["direction"],
                         "threshold": x["threshold"], "unit": x.get("unit"),
                         "company_id": x.get("company_id"),
                         "companies": [{**co(c["company_id"]), "value": c["value"]}
                                       for c in x["companies"]], "more": x.get("more", 0)}
                        for x in d.get("crossed") or []],
            **{k: {"companies": [co(c) for c in d[k]["companies"]], "more": d[k].get("more", 0)}
               for k in ("entered", "left") if k in d},
            "hidden": d.get("hidden", 0), "links": links}


# --- storage ----------------------------------------------------------------------------------

class Pool(Protocol):
    def connection(self) -> Any: ...


class TooMany(Exception):
    """The user has MAX_WATCHES already."""


WATCH_COLUMNS = ("w.watch_id::text, w.source, w.item_id::text, w.entering, w.leaving, w.columns,"
                 " w.lines, w.checked_at, w.outcome, w.companies, w.created_at, w.updated_at,"
                 " i.name")
ALERT_COLUMNS = ("alert_id::text, watch_id::text, item_id::text, name, detail, created_at,"
                 " read_at")


class AppWatches:
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

    def watches(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {WATCH_COLUMNS} FROM watches w"
                        " JOIN workspace_items i ON i.item_id = w.item_id AND i.user_id = w.user_id"
                        " WHERE w.user_id = %s ORDER BY w.created_at, w.watch_id", (user_id,))
            return cur.fetchall()

    def screen(self, user_id: str, item_id: str) -> dict | None:
        """The user's own saved screen (kind, name, state), or None (another user's, gone, or
        not a screen: the routes' 404)."""
        with self._tx(user_id) as cur:
            cur.execute("SELECT item_id::text, kind, name, state FROM workspace_items"
                        " WHERE user_id = %s AND item_id = %s AND kind = 'screen'",
                        (user_id, item_id))
            return cur.fetchone()

    def put(self, user_id: str, item_id: str, entering: bool, leaving: bool,
            columns: list[str], lines: list[dict]) -> dict | None:
        """Creates or replaces the watch on a saved screen; None when it isn't the user's screen.
        What it saw stays: `basis` decides whether the next check only records."""
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("watches:" + user_id,))
                cur.execute("SELECT 1 FROM workspace_items WHERE user_id = %s AND item_id = %s"
                            " AND kind = 'screen'", (user_id, item_id))
                if cur.fetchone() is None:
                    return None
                cur.execute("SELECT count(*) AS n FROM watches WHERE user_id = %s"
                            " AND item_id <> %s", (user_id, item_id))
                if (cur.fetchone() or {"n": 0})["n"] >= MAX_WATCHES:
                    raise TooMany()
                cur.execute("INSERT INTO watches (user_id, item_id, entering, leaving, columns,"
                            " lines) VALUES (%s, %s, %s, %s, %s, %s)"
                            " ON CONFLICT (user_id, item_id) DO UPDATE"
                            " SET entering = EXCLUDED.entering, leaving = EXCLUDED.leaving,"
                            "     columns = EXCLUDED.columns, lines = EXCLUDED.lines,"
                            "     updated_at = statement_timestamp()",
                            (user_id, item_id, entering, leaving, columns, Jsonb(lines)))
                cur.execute(f"SELECT {WATCH_COLUMNS} FROM watches w"
                            " JOIN workspace_items i ON i.item_id = w.item_id"
                            "  AND i.user_id = w.user_id"
                            " WHERE w.user_id = %s AND w.item_id = %s", (user_id, item_id))
                return cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def delete(self, user_id: str, item_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM watches WHERE user_id = %s AND item_id = %s",
                        (user_id, item_id))
            return cur.rowcount == 1

    def alerts(self, user_id: str, item_id: str | None, limit: int) -> list[dict]:
        """The user's screen alerts, newest first (one screen's with item_id)."""
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {ALERT_COLUMNS} FROM screen_alerts"
                        " WHERE user_id = %(u)s AND (%(i)s::uuid IS NULL OR item_id = %(i)s)"
                        " ORDER BY created_at DESC, alert_id LIMIT %(n)s",
                        {"u": user_id, "i": item_id, "n": limit})
            return cur.fetchall()

    # the job (query_api/watch_job.py)
    def due(self, user_id: str, landed: datetime | None, fallback: datetime) -> list[dict]:
        """The user's watches to check now, with their screen (module docstring, "When"):
        never checked, figures landed since, FALLBACK old, or the watch or its screen saved
        since the last check."""
        with self._tx(user_id) as cur:
            cur.execute(
                "SELECT w.watch_id::text, w.source, w.item_id::text, w.entering, w.leaving,"
                "       w.columns, w.lines, w.basis, w.members, w.near, w.checked_at,"
                "       w.updated_at, i.kind, i.name, i.state"
                " FROM watches w"
                " JOIN workspace_items i ON i.item_id = w.item_id AND i.user_id = w.user_id"
                " WHERE w.user_id = %(u)s"
                "   AND (w.checked_at IS NULL OR w.checked_at < %(landed)s"
                "        OR w.checked_at < %(fallback)s OR w.updated_at > w.checked_at"
                "        OR i.updated_at > w.checked_at)"
                " ORDER BY w.checked_at NULLS FIRST, w.watch_id",
                {"u": user_id, "landed": landed, "fallback": fallback})
            return cur.fetchall()

    def record(self, user_id: str, watch: dict, result: dict, at: datetime) -> str | None:
        """Stores a check (`result`: outcome, companies, and for a completed one basis, members,
        near and the alert's detail or None) unless the watch changed meanwhile, and the alert
        with a delivery for each enabled webhook, in one transaction. Returns the alert's id."""
        with self._tx(user_id) as cur:
            if result["outcome"] == "failed":  # what it saw last stays
                cur.execute("UPDATE watches SET checked_at = %s, outcome = 'failed'"
                            " WHERE user_id = %s AND watch_id = %s AND updated_at = %s",
                            (at, user_id, watch["watch_id"], watch["updated_at"]))
                return None
            cur.execute("UPDATE watches SET basis = %s, members = %s, near = %s, checked_at = %s,"
                        " outcome = %s, companies = %s"
                        " WHERE user_id = %s AND watch_id = %s AND updated_at = %s",
                        (result.get("basis"), result.get("members"),
                         Jsonb(result["near"]) if result.get("near") is not None else None,
                         at, result["outcome"], result.get("companies"), user_id,
                         watch["watch_id"], watch["updated_at"]))
            if cur.rowcount != 1 or not result.get("detail"):
                return None  # changed meanwhile (checked again next run), or nothing to say
            cur.execute("INSERT INTO screen_alerts (user_id, watch_id, item_id, name, detail,"
                        " created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING alert_id::text",
                        (user_id, watch["watch_id"], watch["item_id"], watch["name"][:120],
                         Jsonb(result["detail"]), at))
            alert_id = (cur.fetchone() or {})["alert_id"]
            cur.execute("INSERT INTO webhook_deliveries (user_id, webhook_id, screen_alert_id,"
                        " next_attempt_at)"
                        " SELECT %(u)s, w.webhook_id, %(a)s, %(at)s FROM webhooks w"
                        " WHERE w.user_id = %(u)s AND w.enabled"
                        " ON CONFLICT (webhook_id, screen_alert_id) DO NOTHING",
                        {"u": user_id, "a": alert_id, "at": at})
            return alert_id

    def prune(self, user_id: str, before: datetime) -> int:
        """Screen alerts (and their deliveries, by cascade) older than `before`."""
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM screen_alerts WHERE user_id = %s AND created_at < %s",
                        (user_id, before))
            return cur.rowcount


# --- shapes -----------------------------------------------------------------------------------

def watch_out(w: dict) -> dict:
    return {"id": w["watch_id"], "source": w["source"], "item_id": w["item_id"],
            "name": w["name"], "entering": w["entering"], "leaving": w["leaving"],
            "columns": w["columns"] or [], "lines": w["lines"] or [],
            "checked_at": w["checked_at"], "outcome": w["outcome"], "companies": w["companies"],
            "created_at": w["created_at"], "updated_at": w["updated_at"]}


def alert_out(a: dict, can: Callable[[str], bool]) -> dict:
    return {"id": a["alert_id"], "watch_id": a["watch_id"], "item_id": a["item_id"],
            "name": a["name"], **visible(a["detail"] or {}, can),
            "created_at": a["created_at"], "read_at": a["read_at"]}


# --- routes -----------------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineBody(_Strict):
    column: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]
    label: Annotated[str, Field(min_length=1, max_length=LABEL)] | None = None
    direction: Literal["above", "below"]
    threshold: Annotated[str, Field(max_length=40)]
    company_id: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None

    @field_validator("threshold")
    @classmethod
    def _number(cls, v: str) -> str:
        try:
            d = Decimal(v.strip())
        except InvalidOperation:
            raise ValueError("a number, e.g. 0.25 for 25%") from None
        if not d.is_finite() or abs(d) >= Decimal(10) ** 15:
            raise ValueError("a number, e.g. 0.25 for 25%")
        return str(d)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if any(ord(c) < 32 or 127 <= ord(c) < 160 for c in v):
            raise ValueError("no control characters")
        return " ".join(v.split()) or None


class WatchBody(_Strict):
    entering: StrictBool = True
    leaving: StrictBool = True
    # the screener columns the page shows (views); absent: the default set
    columns: list[Annotated[str, Field(pattern=r"^[a-z0-9_]{1,64}$")]] | None = Field(
        default=None, max_length=MAX_SCREENER_COLUMNS)
    lines: list[LineBody] = Field(default_factory=list, max_length=MAX_LINES)


def checked_lines(body: WatchBody, state: dict) -> list[dict]:
    """The lines as stored, or ValueError (words safe to return): a column of the screener's
    catalog or one of the screen's own metrics."""
    own = {metric_key(i) for i in metric_ids(state)}
    out = []
    for line in body.lines:
        if line.column not in COLUMNS and not (_METRIC_KEY.match(line.column)
                                               and line.column in own):
            raise ValueError(f"lines: {line.column!r} isn't a screener column or one of this "
                             "screen's own metrics")
        out.append({"column": line.column, "label": line.label or default_label(line.column),
                    "direction": line.direction, "threshold": line.threshold,
                    "company_id": line.company_id})
    return out


def add_watch_routes(app: FastAPI, *, watches: AppWatches | None,
                     current_user: Callable[..., str],
                     readable: Callable[[str], Readable]) -> None:
    """The /v1/watches routes and /v1/alerts/screens. Without the app database every one answers
    503 (the web app hides watching on a 404 from an older API)."""
    User = Annotated[str, Depends(current_user)]

    def db() -> AppWatches:
        if watches is None:
            raise HTTPException(503, "watches unavailable")
        return watches

    @app.get("/v1/watches", response_model=dict)
    def list_watches(user_id: User) -> dict:
        """The caller's watched screens: what alerts, the lines, and the last check (`outcome`:
        null before the first, baseline, ok, too_broad, failed; `companies` in it then)."""
        return {"watches": [watch_out(w) for w in db().watches(user_id)],
                "max_watches": MAX_WATCHES, "max_lines": MAX_LINES,
                "max_companies": MAX_MEMBERS, "max_listed": MAX_LISTED, "schedule": SCHEDULE}

    @app.put("/v1/watches/screen/{item_id}", response_model=dict)
    def put_watch(user_id: User, item_id: ItemId, body: WatchBody) -> dict:
        """Watch one of the caller's saved screens (or change what alerts). The first check
        after a change only records who's in it."""
        if not (body.entering or body.leaving or body.lines):
            raise HTTPException(422, "watch companies entering, leaving or a line")
        a = db()
        screen = a.screen(user_id, item_id)
        if screen is None:
            raise HTTPException(404, "no such saved screen")
        columns = list(dict.fromkeys(body.columns or []))
        bad = [c for c in columns if c not in COLUMNS]
        if bad:
            raise HTTPException(422, f"columns: unknown {bad}")
        try:
            lines = checked_lines(body, screen["state"] or {})
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        try:
            row = a.put(user_id, item_id, body.entering, body.leaving, columns, lines)
        except TooMany:
            raise HTTPException(409, f"at most {MAX_WATCHES} watched screens") from None
        except UnknownUser:
            raise HTTPException(409, "no account to save a watch to") from None
        if row is None:
            raise HTTPException(404, "no such saved screen")
        return {"watch": watch_out(row)}

    @app.delete("/v1/watches/screen/{item_id}", response_model=dict)
    def delete_watch(user_id: User, item_id: ItemId) -> dict:
        if not db().delete(user_id, item_id):
            raise HTTPException(404, "no such watch")
        return {"deleted": True}

    @app.get("/v1/alerts/screens", response_model=dict)
    def screen_alerts(
        user_id: User,
        item_id: Annotated[str | None, Query(pattern=UUID)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 50,
    ) -> dict:
        """The caller's screen alerts, newest first: companies that entered, left or crossed a
        line, only those whose financials the caller reads now (`hidden` counts the rest)."""
        rows = db().alerts(user_id, item_id, limit + 1)
        if rows:
            access = readable(user_id)
            can: Callable[[str], bool] = lambda c: "financials" in access.get(c, set())  # noqa: E731
        else:
            can = lambda _c: False  # noqa: E731
        return {"alerts": [alert_out(a, can) for a in rows[:limit]],
                "has_more": len(rows) > limit, "schedule": SCHEDULE}

