"""The decision journal (ROADMAP 3): dated entries about a company the user holds or studies (or a
not-covered symbol they hold), kept honest. Storage: query_api/portfolio.py AppPortfolio (app
migration 021's journal_entries); this module the rules and the routes.

  GET    /v1/portfolio/journal?company_id=|symbol=   entries, newest decided first (the day
                                                   they show), then newest written (Morgan 31)
  POST   /v1/portfolio/journal                       a new entry, or a correction of one
  PATCH  /v1/portfolio/journal/{id}                  within EDIT_HOURS of writing it
  DELETE /v1/portfolio/journal/{id}                  within EDIT_HOURS of writing it

Rules (README "Portfolio"; test_journal.py, test_portfolio_db.py):
- An entry: kind (bought, added, trimmed, sold, review, note), the day it was decided (not in
  the future; written today by default), why (required), what would make the user sell
  ("sell rules", free text), optionally tied to their red lines on the company (filing alerts'
  red lines, copied into the entry as they read when it was written: a red line edited or
  deleted later doesn't rewrite what the entry said), and confidence 1-5.
- Kept as written: an entry can be edited or deleted for EDIT_HOURS after it was written; after
  that the database refuses both (021's trigger) and a change is a new entry with `corrects`
  naming the one it corrects (same company or symbol). The API says `kept` and `editable_until`.
- A `review` entry is what clears a holding's review due (query_api/portfolio.py).
- Owner-only, like the rest of the portfolio; another user's entry is the same 404 as none.
"""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from query_api.alerts import display_of
from query_api.portfolio import AppPortfolio, LimitReached, UnknownUser

KINDS = ("bought", "added", "trimmed", "sold", "review", "note")
EDIT_HOURS = 24
MAX_ENTRIES = 5000   # per user
MAX_LIST = 200
MAX_WHY = 4000
MAX_SELL_RULES = 2000
MAX_RED_LINES = 10
EDITABLE = ("kind", "decided_on", "why", "sell_rules", "red_lines", "confidence")
Kind = Literal["bought", "added", "trimmed", "sold", "review", "note"]
Id = Annotated[str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
Uuid = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                    r"[0-9a-f]{12}$")]
KEPT = (f"a journal entry is kept as written after {EDIT_HOURS} hours: add a correction "
        "instead")
NOT_FOUND = "no such journal entry"


def clean_text(v: str, what: str) -> str:
    """The user's own words as written: tabs and newlines kept, other control characters
    refused, leading and trailing space trimmed."""
    if any((ord(c) < 32 and c not in "\n\t\r") or 127 <= ord(c) < 160 for c in v):
        raise ValueError(f"{what}: no control characters")
    return v.replace("\r\n", "\n").strip()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Fields(_Strict):
    kind: Kind | None = None
    why: Annotated[str, Field(max_length=MAX_WHY)] | None = None
    sell_rules: Annotated[str, Field(max_length=MAX_SELL_RULES)] | None = None
    red_line_ids: list[Uuid] | None = Field(None, max_length=MAX_RED_LINES)
    confidence: Annotated[int, Field(ge=1, le=5)] | None = None
    decided_on: date | None = None

    @field_validator("why")
    @classmethod
    def _why(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, "why")

    @field_validator("sell_rules")
    @classmethod
    def _rules(cls, v: str | None) -> str | None:
        return None if v is None else clean_text(v, "sell rules") or None


class EntryBody(_Fields):
    company_id: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9./-]{0,11}$")] | None = None
    kind: Kind
    why: Annotated[str, Field(max_length=MAX_WHY)]
    corrects: Uuid | None = None

    @model_validator(mode="after")
    def _one(self) -> "EntryBody":
        if (self.company_id is None) == (self.symbol is None):
            raise ValueError("give company_id or symbol")
        if not self.why:
            raise ValueError("say why, in a sentence or more")
        if self.symbol and self.red_line_ids:
            raise ValueError("red lines are a company's: a fund or ETF has none")
        return self


class EntryPatch(_Fields):
    """Only the fields sent change; null clears sell_rules, red_line_ids or confidence."""


def entry_out(e: dict, now: datetime) -> dict:
    until = e["created_at"] + timedelta(hours=EDIT_HOURS)
    return {"id": e["entry_id"], "company_id": e["company_id"], "symbol": e["symbol"],
            "kind": e["kind"], "decided_on": e["decided_on"], "why": e["why"],
            "sell_rules": e["sell_rules"], "red_lines": e["red_lines"] or [],
            "confidence": e["confidence"], "corrects": e["corrects"],
            "created_at": e["created_at"], "edited_at": e["edited_at"],
            "editable_until": until, "kept": now >= until}


def snapshot(rows: list[dict]) -> list[dict]:
    """Red lines as the entry keeps them: what they said when it was written."""
    return [{"rule_id": r["rule_id"], "label": r["label"], "formula": r["formula"],
             "view": r["view"], "op": r["op"], "threshold": str(r["threshold"]),
             "display": display_of(r["formula"], r["view"])} for r in rows]


def add_journal_routes(app: FastAPI, *, portfolio: AppPortfolio | None,
                       current_user: Callable[..., str], today: Callable[[], date],
                       clock: Callable[[], datetime]) -> None:
    """The journal routes (module docstring); without the app database each answers 503."""
    User = Annotated[str, Depends(current_user)]

    def db() -> AppPortfolio:
        if portfolio is None:
            raise HTTPException(503, "portfolio unavailable")
        return portfolio

    def red_lines(user_id: str, company_id: str | None, ids: list[str] | None) -> list | None:
        if not ids:
            return None
        if company_id is None:
            raise HTTPException(422, "red lines are a company's: a fund or ETF has none")
        found = db().red_lines(user_id, company_id, sorted(set(ids)))
        if len(found) != len(set(ids)):
            raise HTTPException(422, "a red line isn't one of yours on this company")
        return snapshot(found)

    def day(d: date | None) -> date:
        if d is not None and d > today() + timedelta(days=1):
            raise HTTPException(422, "an entry can't be dated in the future")
        return d or today()

    @app.get("/v1/portfolio/journal", response_model=dict)
    def journal(
        user_id: User,
        company_id: Annotated[str | None, Query(pattern=r"^\d{10}$")] = None,
        symbol: Annotated[str | None, Query(pattern=r"^[A-Z0-9][A-Z0-9./-]{0,11}$")] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = 100,
    ) -> dict:
        """The caller's entries about one company or symbol (or all), newest written first;
        a correction names the entry it corrects."""
        rows = db().entries(user_id, company_id, symbol, limit + 1)
        now = clock()
        return {"entries": [entry_out(e, now) for e in rows[:limit]],
                "has_more": len(rows) > limit, "edit_hours": EDIT_HOURS, "kinds": list(KINDS)}

    @app.post("/v1/portfolio/journal", response_model=dict)
    def add_entry(user_id: User, body: EntryBody) -> dict:
        e = {"company_id": body.company_id, "symbol": body.symbol, "kind": body.kind,
             "decided_on": day(body.decided_on), "why": body.why,
             "sell_rules": body.sell_rules, "confidence": body.confidence,
             "corrects": body.corrects,
             "red_lines": red_lines(user_id, body.company_id, body.red_line_ids)}
        if e["red_lines"] is not None:
            e["red_lines"] = Jsonb(e["red_lines"])
        try:
            row = db().add_entry(user_id, e, MAX_ENTRIES)
        except LimitReached as err:
            raise HTTPException(409, str(err)) from None
        except UnknownUser:
            raise HTTPException(409, "no account to keep a journal in") from None
        if row is None:
            raise HTTPException(404, "the entry to correct isn't one of yours about this "
                                     "company")
        return {"entry": entry_out(row, clock())}

    @app.patch("/v1/portfolio/journal/{entry_id}", response_model=dict)
    def edit_entry(user_id: User, entry_id: Id, body: EntryPatch) -> dict:
        """Within EDIT_HOURS of writing it; after that, 409 (add a correction instead)."""
        store = db()
        current = store.entry(user_id, entry_id)
        if current is None:
            raise HTTPException(404, NOT_FOUND)
        sent = body.model_fields_set
        fields: dict[str, Any] = {}
        if "kind" in sent and body.kind is not None:
            fields["kind"] = body.kind
        if "why" in sent:
            if not body.why:
                raise HTTPException(422, "say why, in a sentence or more")
            fields["why"] = body.why
        if "decided_on" in sent:
            fields["decided_on"] = day(body.decided_on)
        if "sell_rules" in sent:
            fields["sell_rules"] = body.sell_rules
        if "confidence" in sent:
            fields["confidence"] = body.confidence
        if "red_line_ids" in sent:
            snap = red_lines(user_id, current["company_id"], body.red_line_ids)
            fields["red_lines"] = Jsonb(snap) if snap is not None else None
        assert set(fields) <= set(EDITABLE)
        try:
            row = store.edit_entry(user_id, entry_id, fields)
        except psycopg.errors.RestrictViolation:
            raise HTTPException(409, KEPT) from None
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return {"entry": entry_out(row, clock())}

    @app.delete("/v1/portfolio/journal/{entry_id}", response_model=dict)
    def delete_entry(user_id: User, entry_id: Id) -> dict:
        """Within EDIT_HOURS of writing it; after that, 409."""
        try:
            done = db().delete_entry(user_id, entry_id)
        except psycopg.errors.RestrictViolation:
            raise HTTPException(409, KEPT) from None
        if not done:
            raise HTTPException(404, NOT_FOUND)
        return {"deleted": True}
