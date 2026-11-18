"""Portfolio (ROADMAP 3, investor features): what a user holds, linked to the research behind it,
valued only with prices the user gave, and the decision journal (query_api/journal.py) that keeps
them honest. App migration 021 holds the tables; this module the storage and the routes.

Routes (README "Portfolio"; all account routes: no API key reaches them, api_keys.KEY_ROUTES
doesn't list them, so neither does the MCP server; agent-api has no tool for them):

  GET    /v1/portfolio                            holdings, totals, weights, prices, reviews due
  GET    /v1/portfolio/position/{company_id}      the company page's "You hold N shares since"
  POST   /v1/portfolio/holdings                   a company (id or ticker) or a not-covered
                                                  symbol, with its first lot (or a lot more)
  DELETE /v1/portfolio/holdings/{id}              the holding, its lots and sells (never the
                                                  journal: that is kept by company)
  POST   /v1/portfolio/holdings/{id}/lots         a buy;   DELETE /v1/portfolio/lots/{id}
  POST   /v1/portfolio/holdings/{id}/sells        a sale;  DELETE /v1/portfolio/sells/{id}
  PUT    /v1/portfolio/holdings/{id}/price        the user's own price {price, on}; DELETE: none
  PUT    /v1/portfolio/holdings/{id}/link         {project_id} or {thesis_item_id} or {}
  POST   /v1/portfolio/import/preview             a Fidelity positions CSV (the raw text) →
                                                  rows matched to companies; nothing saved
  POST   /v1/portfolio/import                     the rows chosen from the preview
  POST   /v1/portfolio/prices/refresh             with Connect your data: the user's provider
  /v1/portfolio/journal...                        query_api/journal.py

Rules:
- Owner-only: every statement runs as the JWT `sub` (app.user_id for 021's FORCEd row-level
  security) and filters on it; another user's holding, lot, sell or entry is the same 404 as one
  that doesn't exist. Nothing here is logged but error types; account numbers are never read
  (portfolio_import drops them) and a label that looks like one is refused.
- A company is resolved by the Query API's company resolution: an id, or a ticker
  (silver.resolve_company, trying Fidelity's BRK.B as BRK-B). A ticker counts as covered when it
  resolves to a company the caller reads financials for; anything else (an ETF, a fund, a bond)
  is a not-covered position: symbol, the broker's description, shares and cost, no price.
- Maths: query_api/portfolio_maths.py (average cost, realised on sale, values only with the
  user's own price, weights). A write that leaves a sale selling more than was held is refused.
- Prices are the user's own: their Connect-your-data provider's latest stored close (only when
  the server has it on and they connected), else the price they typed or imported; the newer
  wins, the provider on a tie. Each says its source and day; older than STALE_AFTER_DAYS is
  stale. Never fetched by Tributary for itself, never shared across users.
- Reviews: an open covered holding's review falls due when the company files a 10-K or 10-Q
  (original), or an 8-K reporting results (item 2.02: results day is the news; its items from
  the filing index, else the item headings the parser found, with the filings tier, as the alerts
  job reads them), after the holding was added and after the user's last journal entry of kind
  `review` for it. Open holdings also join the filing alerts' follows (source `portfolio`,
  query_api/alerts.py), and the bell counts reviews due (`reviews_due`).
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Protocol
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from query_api import portfolio_import as fidelity
from query_api.authorizer import Readable
from query_api.connections import Reconnect
from query_api.portfolio_maths import (
    METHOD,
    STALE_AFTER_DAYS,
    Oversold,
    Position,
    money,
    number,
    position,
    ratio,
    stale,
    valued,
    weights,
)

ET = ZoneInfo("America/New_York")
CURRENCY = "USD"            # Fidelity's US export and what a user types: one currency, said
MAX_HOLDINGS = 500          # per user
MAX_LOTS = 200              # per holding
MAX_SELLS = 200             # per holding
MAX_BODY = 128 * 1024       # JSON bodies (an import of 500 rows fits)
REVIEW_LOOKBACK_DAYS = 400  # reports older than this never make a review due
CHUNK = 50                  # companies per filing-index query (like the alerts job)
MAX_REFRESH = 25            # holdings asked of the user's provider per refresh
REVIEW_FORMS = ["10-K", "10-Q", "8-K"]
RESULTS_ITEM = "2.02"       # an 8-K's results of operations: it makes a review due
CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]
Id = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
SYMBOL = fidelity.SYMBOL
NOT_FOUND = "no such holding"


class UnknownUser(Exception):
    """No app.users row for the caller."""


class LimitReached(Exception):
    """A cap (MAX_HOLDINGS, MAX_LOTS, MAX_SELLS) is reached; the message says which."""


class Pool(Protocol):
    def connection(self) -> Any: ...


HOLDING_COLUMNS = ("h.holding_id::text, h.company_id, h.symbol, h.name, h.project_id::text,"
                   " h.thesis_item_id::text, h.price, h.price_on, h.price_source, h.created_at,"
                   " h.updated_at")
LOT_COLUMNS = ("lot_id::text, holding_id::text, shares, cost_total, bought_on, account_label,"
               " source, created_at")
SELL_COLUMNS = "sell_id::text, holding_id::text, shares, price, sold_on, created_at"
ENTRY_COLUMNS = ("entry_id::text, company_id, symbol, kind, decided_on, why, sell_rules, red_lines,"
                 " confidence, corrects::text, created_at, edited_at")


class AppPortfolio:
    """Every method is one app-DB transaction as `user_id` (app.user_id for the forced row-level
    security), and every statement filters on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    # --- reads ------------------------------------------------------------------------------
    def holdings(self, user_id: str) -> list[dict]:
        """Every holding with its lots, sells, link (and the link's name) and the user's
        Analysis projects and theses for its company (`candidates`)."""
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {HOLDING_COLUMNS}, w.name AS project_name,"
                        "       i.name AS thesis_name"
                        " FROM portfolio_holdings h"
                        " LEFT JOIN workspaces w ON w.workspace_id = h.project_id"
                        "  AND w.user_id = h.user_id"
                        " LEFT JOIN workspace_items i ON i.item_id = h.thesis_item_id"
                        "  AND i.user_id = h.user_id"
                        " WHERE h.user_id = %s ORDER BY h.created_at, h.holding_id", (user_id,))
            rows = cur.fetchall()
            if not rows:
                return []
            cur.execute(f"SELECT {LOT_COLUMNS} FROM portfolio_lots WHERE user_id = %s"
                        " ORDER BY bought_on NULLS FIRST, created_at, lot_id", (user_id,))
            lots = cur.fetchall()
            cur.execute(f"SELECT {SELL_COLUMNS} FROM portfolio_sells WHERE user_id = %s"
                        " ORDER BY sold_on, created_at, sell_id", (user_id,))
            sells = cur.fetchall()
            ids = sorted({r["company_id"] for r in rows if r["company_id"]})
            cur.execute("SELECT 'project' AS kind, w.workspace_id::text AS id, w.name,"
                        "       w.project->>'companyId' AS company_id, w.created_at"
                        " FROM workspaces w WHERE w.user_id = %(u)s AND w.project IS NOT NULL"
                        "   AND w.project->>'companyId' = ANY(%(ids)s)"
                        " UNION ALL"
                        " SELECT 'thesis', i.item_id::text, i.name, w.project->>'companyId',"
                        "        i.created_at"
                        " FROM workspace_items i JOIN workspaces w"
                        "   ON w.workspace_id = i.workspace_id AND w.user_id = i.user_id"
                        " WHERE i.user_id = %(u)s AND i.kind = 'thesis' AND w.project IS NOT NULL"
                        "   AND w.project->>'companyId' = ANY(%(ids)s)"
                        " ORDER BY created_at", {"u": user_id, "ids": ids})
            candidates = cur.fetchall()
        for r in rows:
            r["lots"] = [x for x in lots if x["holding_id"] == r["holding_id"]]
            r["sells"] = [x for x in sells if x["holding_id"] == r["holding_id"]]
            r["candidates"] = [{"kind": c["kind"], "id": c["id"], "name": c["name"]}
                               for c in candidates if c["company_id"] == r["company_id"]]
        return rows

    def position_of(self, user_id: str, company_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute("SELECT h.holding_id::text, h.created_at FROM portfolio_holdings h"
                        " WHERE h.user_id = %s AND h.company_id = %s", (user_id, company_id))
            h = cur.fetchone()
            if h is None:
                return None
            cur.execute(f"SELECT {LOT_COLUMNS} FROM portfolio_lots"
                        " WHERE user_id = %s AND holding_id = %s", (user_id, h["holding_id"]))
            h["lots"] = cur.fetchall()
            cur.execute(f"SELECT {SELL_COLUMNS} FROM portfolio_sells"
                        " WHERE user_id = %s AND holding_id = %s", (user_id, h["holding_id"]))
            h["sells"] = cur.fetchall()
            return h

    def open_holdings(self, user_id: str) -> list[dict]:
        """company_id, created_at of the open covered holdings (more shares bought than sold)."""
        with self._tx(user_id) as cur:
            cur.execute(OPEN_HOLDINGS, {"u": user_id})
            return cur.fetchall()

    def last_reviews(self, user_id: str, company_ids: list[str]) -> dict[str, datetime]:
        """company_id → when the user last wrote a journal entry of kind review for it."""
        with self._tx(user_id) as cur:
            cur.execute("SELECT company_id, max(created_at) AS at FROM journal_entries"
                        " WHERE user_id = %s AND kind = 'review' AND company_id = ANY(%s)"
                        " GROUP BY company_id", (user_id, company_ids))
            return {r["company_id"]: r["at"] for r in cur.fetchall()}

    # --- holdings, lots, sells --------------------------------------------------------------
    def _holding(self, cur: psycopg.Cursor, user_id: str, holding_id: str) -> dict | None:
        """The holding, locked: writes to one holding's lots and sells take turns."""
        cur.execute("SELECT holding_id::text, company_id, symbol FROM portfolio_holdings"
                    " WHERE user_id = %s AND holding_id = %s FOR UPDATE", (user_id, holding_id))
        return cur.fetchone()

    def _check(self, cur: psycopg.Cursor, user_id: str, holding_id: str) -> Position:
        """Re-runs the maths on the holding's rows as they now are (raises Oversold, which
        rolls the transaction back)."""
        cur.execute(f"SELECT {LOT_COLUMNS} FROM portfolio_lots"
                    " WHERE user_id = %s AND holding_id = %s", (user_id, holding_id))
        lots = cur.fetchall()
        cur.execute(f"SELECT {SELL_COLUMNS} FROM portfolio_sells"
                    " WHERE user_id = %s AND holding_id = %s", (user_id, holding_id))
        return position(lots, cur.fetchall())

    def _find_or_add(self, cur: psycopg.Cursor, user_id: str, company_id: str | None,
                     symbol: str | None, name: str | None) -> tuple[str, bool]:
        if company_id is not None:
            cur.execute("SELECT holding_id::text FROM portfolio_holdings"
                        " WHERE user_id = %s AND company_id = %s FOR UPDATE",
                        (user_id, company_id))
        else:
            cur.execute("SELECT holding_id::text FROM portfolio_holdings"
                        " WHERE user_id = %s AND company_id IS NULL AND symbol = %s FOR UPDATE",
                        (user_id, symbol))
        found = cur.fetchone()
        if found:
            return found["holding_id"], False
        cur.execute("SELECT count(*) AS n FROM portfolio_holdings WHERE user_id = %s", (user_id,))
        if (cur.fetchone() or {"n": 0})["n"] >= MAX_HOLDINGS:
            raise LimitReached(f"at most {MAX_HOLDINGS} holdings")
        cur.execute("INSERT INTO portfolio_holdings (user_id, company_id, symbol, name)"
                    " VALUES (%s, %s, %s, %s) RETURNING holding_id::text",
                    (user_id, company_id, symbol, None if company_id else name))
        return cur.fetchone()["holding_id"], True

    def _insert_lot(self, cur: psycopg.Cursor, user_id: str, holding_id: str, lot: dict) -> dict:
        cur.execute("SELECT count(*) AS n FROM portfolio_lots WHERE user_id = %s"
                    " AND holding_id = %s", (user_id, holding_id))
        if (cur.fetchone() or {"n": 0})["n"] >= MAX_LOTS:
            raise LimitReached(f"at most {MAX_LOTS} buys a holding")
        cur.execute("INSERT INTO portfolio_lots (holding_id, user_id, shares, cost_total,"
                    " bought_on, account_label, source) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    f" RETURNING {LOT_COLUMNS}",
                    (holding_id, user_id, lot["shares"], lot["cost_total"], lot.get("bought_on"),
                     lot.get("account_label"), lot.get("source", "typed")))
        return cur.fetchone()

    def add_holding(self, user_id: str, company_id: str | None, symbol: str | None,
                    name: str | None, lot: dict) -> tuple[str, bool]:
        """A lot for the company (or not-covered symbol), making its holding first if there is
        none: (holding_id, made)."""
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("portfolio:" + user_id,))
                holding_id, made = self._find_or_add(cur, user_id, company_id, symbol, name)
                self._holding(cur, user_id, holding_id)
                self._insert_lot(cur, user_id, holding_id, lot)
                self._check(cur, user_id, holding_id)
                return holding_id, made
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def add_lot(self, user_id: str, holding_id: str, lot: dict) -> dict | None:
        with self._tx(user_id) as cur:
            if self._holding(cur, user_id, holding_id) is None:
                return None
            row = self._insert_lot(cur, user_id, holding_id, lot)
            self._check(cur, user_id, holding_id)
            return row

    def delete_lot(self, user_id: str, lot_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("SELECT holding_id::text FROM portfolio_lots WHERE user_id = %s"
                        " AND lot_id = %s", (user_id, lot_id))
            row = cur.fetchone()
            if row is None or self._holding(cur, user_id, row["holding_id"]) is None:
                return False
            cur.execute("DELETE FROM portfolio_lots WHERE user_id = %s AND lot_id = %s",
                        (user_id, lot_id))
            self._check(cur, user_id, row["holding_id"])
            return True

    def add_sell(self, user_id: str, holding_id: str, sell: dict) -> dict | None:
        with self._tx(user_id) as cur:
            if self._holding(cur, user_id, holding_id) is None:
                return None
            cur.execute("SELECT count(*) AS n FROM portfolio_sells WHERE user_id = %s"
                        " AND holding_id = %s", (user_id, holding_id))
            if (cur.fetchone() or {"n": 0})["n"] >= MAX_SELLS:
                raise LimitReached(f"at most {MAX_SELLS} sales a holding")
            cur.execute("INSERT INTO portfolio_sells (holding_id, user_id, shares, price, sold_on)"
                        f" VALUES (%s, %s, %s, %s, %s) RETURNING {SELL_COLUMNS}",
                        (holding_id, user_id, sell["shares"], sell["price"], sell["sold_on"]))
            row = cur.fetchone()
            self._check(cur, user_id, holding_id)
            # the sale's price is the user's own price of its day (portfolio_maths), unless they
            # hold a newer one; a not-covered position keeps none (Morgan 31)
            cur.execute("UPDATE portfolio_holdings SET price = %s, price_on = %s,"
                        " price_source = 'typed', updated_at = statement_timestamp()"
                        " WHERE user_id = %s AND holding_id = %s AND company_id IS NOT NULL"
                        "   AND (price_on IS NULL OR price_on <= %s)",
                        (sell["price"], sell["sold_on"], user_id, holding_id, sell["sold_on"]))
            return {**row, "priced": cur.rowcount == 1}

    def delete_sell(self, user_id: str, sell_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM portfolio_sells WHERE user_id = %s AND sell_id = %s",
                        (user_id, sell_id))
            return cur.rowcount == 1  # fewer sales can't oversell

    def set_price(self, user_id: str, holding_id: str, price: Decimal | None, on: date | None,
                  source: str | None) -> bool | None:
        """None: no such holding; False: a not-covered position (it takes no price)."""
        with self._tx(user_id) as cur:
            h = self._holding(cur, user_id, holding_id)
            if h is None:
                return None
            if h["company_id"] is None and price is not None:
                return False
            cur.execute("UPDATE portfolio_holdings SET price = %s, price_on = %s,"
                        " price_source = %s, updated_at = statement_timestamp()"
                        " WHERE user_id = %s AND holding_id = %s",
                        (price, on, source, user_id, holding_id))
            return True

    def set_link(self, user_id: str, holding_id: str, project_id: str | None,
                 thesis_item_id: str | None) -> bool | None:
        """None: no such holding; False: the project or thesis isn't one of the user's (a
        project must be an Analysis project, a thesis an item of kind thesis)."""
        with self._tx(user_id) as cur:
            if self._holding(cur, user_id, holding_id) is None:
                return None
            if project_id is not None:
                cur.execute("SELECT 1 FROM workspaces WHERE user_id = %s AND workspace_id = %s"
                            " AND project IS NOT NULL", (user_id, project_id))
                if cur.fetchone() is None:
                    return False
            if thesis_item_id is not None:
                cur.execute("SELECT 1 FROM workspace_items WHERE user_id = %s AND item_id = %s"
                            " AND kind = 'thesis'", (user_id, thesis_item_id))
                if cur.fetchone() is None:
                    return False
            cur.execute("UPDATE portfolio_holdings SET project_id = %s, thesis_item_id = %s,"
                        " updated_at = statement_timestamp()"
                        " WHERE user_id = %s AND holding_id = %s",
                        (project_id, thesis_item_id, user_id, holding_id))
            return True

    def delete_holding(self, user_id: str, holding_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM portfolio_holdings WHERE user_id = %s AND holding_id = %s",
                        (user_id, holding_id))
            return cur.rowcount == 1

    def import_positions(self, user_id: str, positions: Sequence[dict],
                         price_on: date | None) -> dict:
        """Each position a lot (source fidelity, no day) on its company's or symbol's holding,
        and a covered one's export price as the user's own price unless they have a newer one.
        One transaction: all or nothing."""
        made = lots = priced = 0
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("portfolio:" + user_id,))
                for p in positions:
                    holding_id, new = self._find_or_add(cur, user_id, p["company_id"],
                                                        p["symbol"], p.get("name"))
                    made += new
                    self._insert_lot(cur, user_id, holding_id, {
                        "shares": p["shares"], "cost_total": p["cost_total"], "bought_on": None,
                        "account_label": p.get("account_label"), "source": "fidelity"})
                    lots += 1
                    if p["company_id"] and p.get("last_price") is not None and price_on:
                        cur.execute("UPDATE portfolio_holdings SET price = %s, price_on = %s,"
                                    " price_source = 'fidelity',"
                                    " updated_at = statement_timestamp()"
                                    " WHERE user_id = %s AND holding_id = %s"
                                    "   AND (price_on IS NULL OR price_on <= %s)",
                                    (p["last_price"], price_on, user_id, holding_id, price_on))
                        priced += cur.rowcount
                return {"holdings_added": made, "lots_added": lots, "prices_saved": priced}
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    # --- the journal (query_api/journal.py has its rules) -----------------------------------
    def entries(self, user_id: str, company_id: str | None, symbol: str | None,
                limit: int) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {ENTRY_COLUMNS} FROM journal_entries"
                        " WHERE user_id = %(u)s"
                        "   AND (%(c)s::text IS NULL OR company_id = %(c)s)"
                        "   AND (%(s)s::text IS NULL OR (company_id IS NULL AND symbol = %(s)s))"
                        " ORDER BY decided_on DESC, created_at DESC, entry_id LIMIT %(n)s",
                        {"u": user_id, "c": company_id, "s": symbol, "n": limit})
            return cur.fetchall()

    def entry(self, user_id: str, entry_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {ENTRY_COLUMNS} FROM journal_entries"
                        " WHERE user_id = %s AND entry_id = %s", (user_id, entry_id))
            return cur.fetchone()

    def red_lines(self, user_id: str, company_id: str, rule_ids: list[str]) -> list[dict]:
        """The user's red lines on the company among `rule_ids` (filing alerts' table, 016)."""
        with self._tx(user_id) as cur:
            cur.execute("SELECT rule_id::text, label, formula, view, op, threshold"
                        " FROM red_lines WHERE user_id = %s AND company_id = %s"
                        "   AND rule_id = ANY(%s::uuid[]) ORDER BY created_at, rule_id",
                        (user_id, company_id, rule_ids))
            return cur.fetchall()

    def add_entry(self, user_id: str, e: dict, max_entries: int) -> dict | None:
        """None: `corrects` isn't one of the user's entries about the same company or symbol."""
        try:
            with self._tx(user_id) as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("journal:" + user_id,))
                cur.execute("SELECT count(*) AS n FROM journal_entries WHERE user_id = %s",
                            (user_id,))
                if (cur.fetchone() or {"n": 0})["n"] >= max_entries:
                    raise LimitReached(f"at most {max_entries} journal entries")
                if e.get("corrects"):
                    cur.execute("SELECT 1 FROM journal_entries WHERE user_id = %s"
                                " AND entry_id = %s AND company_id IS NOT DISTINCT FROM %s"
                                " AND (company_id IS NOT NULL OR symbol = %s)",
                                (user_id, e["corrects"], e["company_id"], e["symbol"]))
                    if cur.fetchone() is None:
                        return None
                cur.execute("INSERT INTO journal_entries (user_id, company_id, symbol, kind,"
                            " decided_on, why, sell_rules, red_lines, confidence, corrects)"
                            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                            f" RETURNING {ENTRY_COLUMNS}",
                            (user_id, e["company_id"], e["symbol"], e["kind"], e["decided_on"],
                             e["why"], e.get("sell_rules"), e.get("red_lines"),
                             e.get("confidence"), e.get("corrects")))
                return cur.fetchone()
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def edit_entry(self, user_id: str, entry_id: str, fields: dict) -> dict | None:
        """Raises psycopg.errors.RestrictViolation after 24 hours (021's trigger)."""
        if not fields:
            return self.entry(user_id, entry_id)
        sets = ", ".join(f"{k} = %({k})s" for k in fields)  # keys: journal.EDITABLE only
        with self._tx(user_id) as cur:
            cur.execute(f"UPDATE journal_entries SET {sets}, edited_at = statement_timestamp()"
                        " WHERE user_id = %(u)s AND entry_id = %(id)s"
                        f" RETURNING {ENTRY_COLUMNS}", {**fields, "u": user_id, "id": entry_id})
            return cur.fetchone()

    def delete_entry(self, user_id: str, entry_id: str) -> bool:
        """Raises psycopg.errors.RestrictViolation after 24 hours (021's trigger)."""
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM journal_entries WHERE user_id = %s AND entry_id = %s",
                        (user_id, entry_id))
            return cur.rowcount == 1


# open covered holdings: more shares bought than sold (a crumb under portfolio_maths.DUST is none);
# alerts.py's follows reads the same rule
OPEN_HOLDINGS = """
    SELECT h.company_id, h.created_at
    FROM portfolio_holdings h
    WHERE h.user_id = %(u)s AND h.company_id IS NOT NULL
      AND (SELECT coalesce(sum(l.shares), 0) FROM portfolio_lots l
           WHERE l.user_id = %(u)s AND l.holding_id = h.holding_id)
        - (SELECT coalesce(sum(s.shares), 0) FROM portfolio_sells s
           WHERE s.user_id = %(u)s AND s.holding_id = h.holding_id) > 0.000001
    ORDER BY h.company_id
"""


# --- request bodies -----------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Shares = Annotated[Decimal, Field(gt=0, lt=Decimal(10) ** 12, allow_inf_nan=False)]
Money = Annotated[Decimal, Field(ge=0, lt=Decimal(10) ** 15, allow_inf_nan=False)]
Price = Annotated[Decimal, Field(gt=0, lt=Decimal(10) ** 9, allow_inf_nan=False)]
Uuid = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                    r"[0-9a-f]{12}$")]


def _symbol(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip().upper()
    if not SYMBOL.fullmatch(s):
        raise ValueError("a ticker or fund symbol: letters, digits, . / - (up to 12)")
    return s


class LotBody(_Strict):
    shares: Shares
    cost_total: Money | None = None       # what the whole buy cost
    cost_per_share: Money | None = None   # or a share's price (times shares)
    bought_on: date | None = None
    account_label: Annotated[str, Field(max_length=200)] | None = None

    @model_validator(mode="after")
    def _one_cost(self) -> "LotBody":
        if (self.cost_total is None) == (self.cost_per_share is None):
            raise ValueError("give cost_total or cost_per_share")
        return self

    def row(self, today: date) -> dict:
        if self.bought_on is not None and self.bought_on > today + timedelta(days=1):
            raise HTTPException(422, "a buy can't be dated in the future")
        cost = self.cost_total if self.cost_total is not None \
            else (self.cost_per_share or Decimal(0)) * self.shares
        return {"shares": self.shares, "cost_total": cost, "bought_on": self.bought_on,
                "account_label": account_label(self.account_label), "source": "typed"}


def account_label(raw: str | None) -> str | None:
    """A typed label, cleaned; refused (422, without echoing it) when it looks like it carries
    an account number."""
    if raw is None or not raw.strip():
        return None
    clean = fidelity.clean_label(raw)
    if clean is None:
        raise HTTPException(422, "an account label can't hold an account number: use a name "
                                 "such as Brokerage or Roth IRA")
    return clean


class HoldingBody(_Strict):
    company_id: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    # a ticker (resolved to a company) or a fund's symbol (not covered)
    symbol: Annotated[str, Field(max_length=20)] | None = None
    name: Annotated[str, Field(max_length=120)] | None = None  # a not-covered position's
    lot: LotBody

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str | None) -> str | None:
        return _symbol(v)

    @model_validator(mode="after")
    def _one(self) -> "HoldingBody":
        if (self.company_id is None) == (self.symbol is None):
            raise ValueError("give company_id or symbol")
        return self


class SellBody(_Strict):
    shares: Shares
    price: Annotated[Decimal, Field(ge=0, lt=Decimal(10) ** 9, allow_inf_nan=False)]
    sold_on: date


class PriceBody(_Strict):
    price: Price
    on: date


class LinkBody(_Strict):
    project_id: Uuid | None = None
    thesis_item_id: Uuid | None = None

    @model_validator(mode="after")
    def _at_most_one(self) -> "LinkBody":
        if self.project_id and self.thesis_item_id:
            raise ValueError("link a project or a thesis, not both")
        return self


class ImportRow(_Strict):
    symbol: Annotated[str, Field(max_length=20)]
    description: Annotated[str, Field(max_length=200)] | None = None
    shares: Shares
    cost_total: Money
    last_price: Price | None = None
    account_label: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        return _symbol(v) or ""


class ImportBody(_Strict):
    positions: list[ImportRow] = Field(min_length=1, max_length=fidelity.MAX_ROWS)
    # the export's day: its Last Price is kept as the user's own price from that day
    price_on: date | None = None


# --- shapes -------------------------------------------------------------------------------------

def _bad(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": message, "code": code}, status_code=status)


def lot_out(r: dict) -> dict:
    return {"id": r["lot_id"], "shares": number(r["shares"]), "cost_total": money(r["cost_total"]),
            "cost_per_share": number(r["cost_total"] / r["shares"]) if r["shares"] else None,
            "bought_on": r["bought_on"], "account_label": r["account_label"],
            "source": r["source"], "created_at": r["created_at"]}


def sell_out(r: dict) -> dict:
    return {"id": r["sell_id"], "shares": number(r["shares"]), "price": number(r["price"]),
            "proceeds": money(r["shares"] * r["price"]), "sold_on": r["sold_on"],
            "created_at": r["created_at"]}


def filed_at(r: dict) -> datetime:
    """A filing's moment: EDGAR's acceptance time, else its filing day's midnight ET."""
    if r.get("accepted_at"):
        return r["accepted_at"]
    return datetime.combine(r["filing_date"], time(0), ET)


def due_reviews(open_holdings: list[dict], reports: list[dict],
                last_reviews: dict[str, datetime]) -> dict[str, dict]:
    """company_id → the report a review is due for (module docstring): the newest 10-K/10-Q,
    or 8-K with results (item 2.02), filed after the holding was added and after the last review
    entry."""
    added = {h["company_id"]: h["created_at"] for h in open_holdings}
    out: dict[str, dict] = {}
    for r in sorted(reports, key=filed_at, reverse=True):
        cid = r["company_id"]
        if cid in out or cid not in added:
            continue
        if r["form"].startswith("8-K") and RESULTS_ITEM not in (r.get("items") or []):
            continue
        at = filed_at(r)
        reviewed = last_reviews.get(cid)
        if at > added[cid] and (reviewed is None or at > reviewed):
            out[cid] = {"accession_no": r["accession_no"], "form": r["form"],
                        "filed": r["filing_date"], "period_end": r.get("period_end"),
                        "cik": r.get("cik")}
    return out


def add_portfolio_routes(app: FastAPI, *, portfolio: AppPortfolio | None, store: Any,
                         current_user: Callable[..., str],
                         readable: Callable[[str], Readable],
                         today: Callable[[], date],
                         own_prices: Callable[[str, list[str]], dict[str, dict]] | None = None,
                         refresh_prices: Callable[[str, dict[str, str]], dict] | None = None,
                         clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                         ) -> Callable[[str], int]:
    """The /v1/portfolio routes (module docstring). Without the app database each answers 503.
    `own_prices(user, company_ids)` → company_id → {close, day, provider}: the user's own
    market-data closes (Connect your data), None when the server has it off; `refresh_prices`
    asks their provider again for {company_id: ticker}. Returns `reviews_due(user)`, the bell's
    count (query_api/alerts.py)."""
    User = Annotated[str, Depends(current_user)]

    @app.middleware("http")
    async def cap_body(request: Request, call_next):
        """Small JSON bodies, and an export of at most portfolio_import.MAX_BYTES: refused before
        they're read."""
        if request.method in ("POST", "PUT", "PATCH") \
                and request.url.path.startswith("/v1/portfolio"):
            cap = fidelity.MAX_BYTES if request.url.path == "/v1/portfolio/import/preview" \
                else MAX_BODY
            size = request.headers.get("content-length")
            if size is None or not size.isdigit():
                if request.headers.get("transfer-encoding"):
                    return JSONResponse({"detail": "send a Content-Length"}, status_code=411)
            elif int(size) > cap:
                return JSONResponse({"detail": f"body larger than {cap} bytes"},
                                    status_code=413)
        return await call_next(request)

    def db() -> AppPortfolio:
        if portfolio is None:
            raise HTTPException(503, "portfolio unavailable")
        return portfolio

    def write(f: Callable[[], Any]) -> Any:
        try:
            return f()
        except Oversold as e:
            raise HTTPException(409, str(e)) from None
        except LimitReached as e:
            raise HTTPException(409, str(e)) from None
        except UnknownUser:
            raise HTTPException(409, "no account to save a portfolio to") from None
        except psycopg.errors.CheckViolation:
            raise HTTPException(422, "a value is out of range") from None

    def classify(user_id: str, symbols: list[str]) -> dict[str, str | None]:
        """symbol → company_id when it resolves to a company the caller reads financials for
        (covered), else None (module docstring)."""
        tries = {s: fidelity.ticker_variants(s) for s in symbols}
        every = sorted({t for ts in tries.values() for t in ts})
        found = store.resolve_tickers(user_id, every) if every else {}
        access = readable(user_id) if found else None
        out: dict[str, str | None] = {}
        for s, ts in tries.items():
            cid = next((found[t] for t in ts if t in found), None)
            out[s] = cid if cid and access and "financials" in access.get(cid, set()) else None
        return out

    def reports_for(user_id: str, open_holdings: list[dict]) -> list[dict]:
        if not open_holdings:
            return []
        earliest = min(h["created_at"] for h in open_holdings).astimezone(ET).date()
        since = max(earliest, today() - timedelta(days=REVIEW_LOOKBACK_DAYS))
        ids = [h["company_id"] for h in open_holdings]
        out: list[dict] = []
        for i in range(0, len(ids), CHUNK):
            out += store.alert_filings(user_id, ids[i:i + CHUNK], REVIEW_FORMS, since)
        # an 8-K the nightly submissions load hasn't itemised yet: the parser's item headings,
        # with the filings tier (alert_job.complete's rule), so results day counts that day
        unknown = [r for r in out if r["form"].startswith("8-K") and not r.get("items")]
        access = readable(user_id) if unknown else None
        for r in unknown:
            if access is None or "filings" not in access.get(r["company_id"], set()):
                continue
            parsed = [e for e in store.company_8ks(user_id, r["company_id"], r["filing_date"])
                      if e["accession_no"] == r["accession_no"]]
            if parsed:
                r["items"] = parsed[0].get("items") or parsed[0].get("parsed_items") or None
        return out

    def reviews(user_id: str, open_holdings: list[dict]) -> dict[str, dict]:
        if not open_holdings:
            return {}
        last = db().last_reviews(user_id, [h["company_id"] for h in open_holdings])
        return due_reviews(open_holdings, reports_for(user_id, open_holdings), last)

    def reviews_due(user_id: str) -> int:
        return len(reviews(user_id, db().open_holdings(user_id)))

    def price_of(h: dict, own: dict[str, dict]) -> dict | None:
        """The user's own price for the holding (module docstring): the newer of their
        provider's close and the price they typed or imported, the provider on a tie."""
        typed = ({"value": h["price"], "on": h["price_on"], "source": h["price_source"],
                  "provider": None} if h["price"] is not None else None)
        mine = own.get(h["company_id"] or "")
        provider = ({"value": Decimal(mine["close"]), "on": mine["day"], "source": "provider",
                     "provider": mine["provider"]} if mine else None)
        best = provider if provider and (typed is None or provider["on"] >= typed["on"]) \
            else typed
        if best is None:
            return None
        return {**best, "stale": stale(best["on"], today())}

    @app.get("/v1/portfolio", response_model=dict)
    def get_portfolio(user_id: User) -> dict:
        """The caller's holdings: shares, cost basis, realised gain; value and unrealised gain
        only on the caller's own prices (source and day said); weights; next expected report;
        the linked thesis or project; a review due after a new 10-K/10-Q."""
        rows = db().holdings(user_id)
        ids = sorted({r["company_id"] for r in rows if r["company_id"]})
        profiles = store.profiles(user_id, ids) if ids else {}
        tickers = store.tickers(user_id, ids) if ids else {}
        out: list[dict] = []
        opened: list[dict] = []
        for r in rows:
            p = position(r["lots"], r["sells"])
            if p.open and r["company_id"]:
                opened.append({"company_id": r["company_id"], "created_at": r["created_at"]})
            out.append({"row": r, "p": p})
        open_ids = [h["company_id"] for h in opened]
        own = own_prices(user_id, open_ids) if own_prices and open_ids else {}
        nxt = {e["company_id"]: e for e in
               (store.expected_filings(user_id, open_ids, today(), 1) if open_ids else [])}
        due = reviews(user_id, opened)
        holdings: list[dict] = []
        for x in out:
            r, p = x["row"], x["p"]
            cid = r["company_id"]
            price = price_of(r, own) if cid and p.open else None
            v = valued(p, price["value"] if price else None)
            e = nxt.get(cid or "")
            link = ({"kind": "project", "id": r["project_id"], "name": r["project_name"]}
                    if r["project_id"] else
                    {"kind": "thesis", "id": r["thesis_item_id"], "name": r["thesis_name"]}
                    if r["thesis_item_id"] else None)
            holdings.append({
                "id": r["holding_id"], "company_id": cid, "covered": cid is not None,
                "symbol": (tickers.get(cid or "") or [r["symbol"]])[0] if cid else r["symbol"],
                "name": profiles.get(cid, {}).get("name") if cid else r["name"],
                "added_at": r["created_at"], "open": p.open,
                "shares": number(p.shares), "cost_basis": money(p.cost),
                "average_cost": number(p.cost / p.shares) if p.open else None,
                "realised": money(p.realised), "first_bought": p.first_bought,
                "price": ({"value": number(price["value"]), "on": price["on"],
                           "source": price["source"], "provider": price["provider"],
                           "stale": price["stale"]} if price else None),
                "value": money(v["value"]), "unrealised": money(v["unrealised"]),
                "unrealised_pct": ratio(v["unrealised_pct"]), "weight": None,
                "next_report": ({"form": e["form"], "period_end": e["period_end"],
                                 "expected_date": e["expected_date"]} if e else None),
                "link": link, "candidates": r["candidates"],
                "review_due": due.get(cid or ""),
                "lots": [lot_out(lot) for lot in r["lots"]],
                "sells": [sell_out(s) for s in r["sells"]],
                "_p": p, "_v": v["value"]})
        basis, w = weights((h["id"], h["_p"].cost, h["_v"]) for h in holdings if h["_p"].open)
        totals = {"cost_basis": Decimal(0), "value": Decimal(0), "unrealised": Decimal(0),
                  "realised": Decimal(0), "unpriced_cost": Decimal(0)}
        priced = 0
        for h in holdings:
            p = h.pop("_p")
            value = h.pop("_v")
            h["weight"] = ratio(w.get(h["id"]))
            totals["realised"] += p.realised
            if p.open:
                totals["cost_basis"] += p.cost
            if value is not None:
                priced += 1
                totals["value"] += value
                totals["unrealised"] += value - p.cost
            elif p.open:
                totals["unpriced_cost"] += p.cost
        open_n = sum(1 for h in holdings if h["open"])
        return {"holdings": holdings, "currency": CURRENCY, "method": METHOD,
                "weight_basis": basis, "stale_after_days": STALE_AFTER_DAYS, "today": today(),
                "totals": {"open": open_n, "priced": priced,
                           "cost_basis": money(totals["cost_basis"]),
                           "value": money(totals["value"]) if priced else None,
                           "unrealised": money(totals["unrealised"]) if priced else None,
                           "realised": money(totals["realised"]),
                           # Morgan 33: the whole portfolio on the user's prices, an open holding
                           # without one at its cost (`unpriced_cost` says how much)
                           "total_value": money(totals["value"] + totals["unpriced_cost"])
                           if open_n else None,
                           "unpriced_cost": money(totals["unpriced_cost"])},
                "reviews_due": len(due),
                "prices_connected": own_prices is not None,
                "refresh": refresh_prices is not None}

    @app.get("/v1/portfolio/position/{company_id}", response_model=dict)
    def get_position(user_id: User, company_id: CompanyId) -> dict:
        """Whether the caller holds the company: shares and since when (the company page)."""
        h = db().position_of(user_id, company_id)
        if h is None:
            return {"position": None}
        p = position(h["lots"], h["sells"])
        return {"position": {"holding_id": h["holding_id"], "shares": number(p.shares),
                             "open": p.open, "first_bought": p.first_bought,
                             "added_at": h["created_at"]}}

    @app.post("/v1/portfolio/holdings", response_model=dict)
    def add_holding(user_id: User, body: HoldingBody) -> dict:
        """A buy: of a company (company_id, or a ticker resolved to one) or of a position
        Tributary doesn't cover (a fund's symbol). Adds to the holding if there is one."""
        lot = body.lot.row(today())
        company_id, symbol, name = body.company_id, body.symbol, None
        if company_id is not None:
            if not store.profiles(user_id, [company_id]):
                raise HTTPException(422, "no such company")
            symbol = (store.tickers(user_id, [company_id]).get(company_id) or [None])[0]
            if symbol is not None and not SYMBOL.fullmatch(symbol):
                symbol = None
        else:
            assert symbol is not None
            company_id = classify(user_id, [symbol])[symbol]
            name = " ".join(body.name.split()) if body.name and body.name.strip() else None
        holding_id, made = write(lambda: db().add_holding(user_id, company_id, symbol, name, lot))
        return {"holding_id": holding_id, "created": made, "covered": company_id is not None,
                "company_id": company_id}

    @app.delete("/v1/portfolio/holdings/{holding_id}", response_model=dict)
    def delete_holding(user_id: User, holding_id: Id) -> dict:
        if not db().delete_holding(user_id, holding_id):
            raise HTTPException(404, NOT_FOUND)
        return {"deleted": True}

    @app.post("/v1/portfolio/holdings/{holding_id}/lots", response_model=dict)
    def add_lot(user_id: User, holding_id: Id, body: LotBody) -> dict:
        row = write(lambda: db().add_lot(user_id, holding_id, body.row(today())))
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {"lot": lot_out(row)}

    @app.delete("/v1/portfolio/lots/{lot_id}", response_model=dict)
    def delete_lot(user_id: User, lot_id: Id) -> dict:
        if not write(lambda: db().delete_lot(user_id, lot_id)):
            raise HTTPException(404, "no such buy")
        return {"deleted": True}

    @app.post("/v1/portfolio/holdings/{holding_id}/sells", response_model=dict)
    def add_sell(user_id: User, holding_id: Id, body: SellBody) -> dict:
        if body.sold_on > today() + timedelta(days=1):
            raise HTTPException(422, "a sale can't be dated in the future")
        row = write(lambda: db().add_sell(user_id, holding_id, body.model_dump()))
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        # `price_recorded`: the sale's price is now the holding's own price of that day
        return {"sell": sell_out(row), "price_recorded": bool(row.get("priced"))}

    @app.delete("/v1/portfolio/sells/{sell_id}", response_model=dict)
    def delete_sell(user_id: User, sell_id: Id) -> dict:
        if not db().delete_sell(user_id, sell_id):
            raise HTTPException(404, "no such sale")
        return {"deleted": True}

    @app.put("/v1/portfolio/holdings/{holding_id}/price", response_model=dict)
    def put_price(user_id: User, holding_id: Id, body: PriceBody) -> dict:
        """The caller's own price a share and its day ('typed'): used until a newer one."""
        if body.on > today() + timedelta(days=1):
            raise HTTPException(422, "a price can't be dated in the future")
        done = write(lambda: db().set_price(user_id, holding_id, body.price, body.on, "typed"))
        if done is None:
            raise HTTPException(404, NOT_FOUND)
        if done is False:
            raise HTTPException(409, "Tributary doesn't cover this fund or ETF, so it keeps no "
                                     "price for it")
        return {"price": {"value": number(body.price), "on": body.on, "source": "typed"}}

    @app.delete("/v1/portfolio/holdings/{holding_id}/price", response_model=dict)
    def delete_price(user_id: User, holding_id: Id) -> dict:
        if db().set_price(user_id, holding_id, None, None, None) is None:
            raise HTTPException(404, NOT_FOUND)
        return {"deleted": True}

    @app.put("/v1/portfolio/holdings/{holding_id}/link", response_model=dict)
    def put_link(user_id: User, holding_id: Id, body: LinkBody) -> dict:
        """Links the holding to one of the caller's Analysis projects or theses ({} unlinks)."""
        done = db().set_link(user_id, holding_id, body.project_id, body.thesis_item_id)
        if done is None:
            raise HTTPException(404, NOT_FOUND)
        if done is False:
            raise HTTPException(404, "no such project or thesis")
        return {"project_id": body.project_id, "thesis_item_id": body.thesis_item_id}

    @app.post("/v1/portfolio/import/preview", response_model=dict)
    async def import_preview(request: Request, user_id: User):
        """A Fidelity "Portfolio Positions" CSV, as its raw text: the positions it holds, each
        matched to a company or marked not covered, what was skipped and why, and what the
        caller already holds. Nothing is saved; account numbers are dropped on parse and never
        returned. Parsed by hand: an error never quotes the file."""
        raw = await request.body()
        if len(raw) > fidelity.MAX_BYTES:
            return _bad(413, "too_large", f"the file is over {fidelity.MAX_BYTES // 1024} KB")
        try:
            parsed = fidelity.parse(raw.decode("utf-8-sig", "replace"))
        except fidelity.NotAnExport as e:
            return _bad(422, "not_an_export", str(e))
        symbols = sorted({p.symbol for p in parsed.positions})
        covered = classify(user_id, symbols)
        ids = sorted({c for c in covered.values() if c})
        profiles = store.profiles(user_id, ids) if ids else {}
        mine_all = db().holdings(user_id)
        by_company = {r["company_id"]: r for r in mine_all if r["company_id"]}
        by_symbol = {r["symbol"]: r for r in mine_all if not r["company_id"]}
        positions = []
        for i, p in enumerate(parsed.positions):
            cid = covered.get(p.symbol)
            mine = by_company.get(cid) if cid else by_symbol.get(p.symbol)
            have = position(mine["lots"], mine["sells"]) if mine else None
            positions.append({
                "row": i, "symbol": p.symbol, "description": p.description,
                "shares": number(p.shares), "cost_total": money(p.cost_total),
                "last_price": number(p.last_price), "account_label": p.account_label,
                "company_id": cid, "name": profiles.get(cid, {}).get("name") if cid else None,
                "covered": cid is not None,
                "held": ({"holding_id": mine["holding_id"], "shares": number(have.shares)}
                         if mine and have else None)})
        return {"positions": positions, "skipped": parsed.skipped,
                "downloaded": parsed.downloaded, "source": "fidelity"}

    @app.post("/v1/portfolio/import", response_model=dict)
    def import_positions(user_id: User, body: ImportBody) -> dict:
        """Saves the positions chosen from a preview: each a buy (no day: the export has none)
        on its company's or symbol's holding; a covered one's Last Price kept as the caller's
        own price of `price_on`. Companies are matched again here, never taken from the body."""
        if body.price_on and body.price_on > today() + timedelta(days=1):
            raise HTTPException(422, "the export can't be dated in the future")
        covered = classify(user_id, sorted({p.symbol for p in body.positions}))
        rows = [{"company_id": covered.get(p.symbol), "symbol": p.symbol,
                 "name": " ".join((p.description or "").split())[:120] or None,
                 "shares": p.shares, "cost_total": p.cost_total, "last_price": p.last_price,
                 "account_label": account_label(p.account_label)} for p in body.positions]
        return write(lambda: db().import_positions(user_id, rows, body.price_on))

    if refresh_prices is not None:
        @app.post("/v1/portfolio/prices/refresh", response_model=dict)
        def refresh(user_id: User) -> dict:
            """Asks the caller's own market-data provider for their open holdings' closes (at
            most once a day each; the connection's hourly limit applies)."""
            ids = [h["company_id"] for h in db().open_holdings(user_id)]
            tickers = store.tickers(user_id, ids) if ids else {}
            return refresh_prices(user_id, {c: t[0] for c, t in tickers.items() if t})

    return reviews_due


def own_prices_from(market: Any) -> tuple[Callable[[str, list[str]], dict[str, dict]],
                                          Callable[[str, dict[str, str]], dict]]:
    """The portfolio's view of Connect your data (query_api/connections.py): the caller's latest
    stored closes, and a refresh through their own key. Only built when the server has it on."""
    def latest(user_id: str, company_ids: list[str]) -> dict[str, dict]:
        provider = market.provider_of(user_id)
        if provider is None:
            return {}
        rows = market.connections.latest_closes(user_id, provider, company_ids)
        return {c: {**r, "provider": provider} for c, r in rows.items()}

    def refresh(user_id: str, tickers: dict[str, str]) -> dict:
        provider = market.provider_of(user_id)
        if provider is None:
            raise HTTPException(404, "connect your market data first (Settings → Connections)")
        asked = warnings = 0
        for cid, ticker in sorted(tickers.items())[:MAX_REFRESH]:
            try:
                f = market.fresh(user_id, provider, cid, ticker)
            except Reconnect:
                raise HTTPException(409, "your market-data key no longer works: reconnect "
                                         "it in Settings → Connections") from None
            asked += 1
            warnings += f.warning is not None
        return {"asked": asked, "warnings": warnings, "provider": provider}

    return latest, refresh

