"""Thesis checks (app migration 010): the history of "Check my thesis" runs, one row per check,
kept with the thesis it checked (services/agent `thesis_check.py` writes them).

Why a table and not the thesis's item state: the thesis state is the user's own text, written
through the generic item routes, so anything stored there could be written by the browser (a
forged "supported" verdict); a check's result is up to ~100 KB and a history of 20 would not fit
the 128 KiB a thesis may hold; and each check must stay readable after the text changes (the
stale notice compares the check's `text_hash` with the text as it is now).

- `POST /v1/thesis-checks/{item_id}` stores one: agent-api calls it with the user's token after
  the run; nginx 404s `/v1/thesis-checks` on the public site, like the report routes. The item
  must be the caller's own thesis (anything else is the same 404). The result is validated as
  strictly as a report (`CheckResult` below): every "supported" or "contradicted" claim and
  every "not mentioned" point carries a citation of a filing the tools returned, the figure
  verification's `ok` is recomputed here, sizes are capped. The newest MAX_CHECKS per thesis
  are kept.
- `GET /v1/workspace-items/{item_id}/checks` lists them (newest first, counts only, plus
  what Revisit needs: the thesis's company, `checked_at` and the result's `filings`) and
  `GET /v1/workspace-items/{item_id}/checks/{check_id}` returns one with its result.

Revisit: `filings` (optional, so results stored before it validate and read as they were)
records per company in the check's scope the newest 10-K/10-Q in the SEC index when the check
ran (`latest`) and the newest filing the result's verified citations rely on (`evidence`,
which must be one of those citations). `version` stays 1: the field is additive.

Owner-only in both layers like every saved item: statements filter on the JWT `sub` and the
table's row-level security is FORCEd on `app.user_id`. A check grants nothing: it holds what
the agent wrote from data the user could read, and filing citations."""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from query_api.reports import (
    ACCESSION,
    SECTION_KEY,
    TIMESTAMP,
    ReportCitation,
    ReportFigure,
)
from query_api.workspaces import CIK, ISO_DATE, ITEM_NAME, StateError, clean_name

MAX_CHECKS = 20  # per thesis; older ones are deleted when a new one is stored
MAX_CHECK_BYTES = 120_000  # the result as compact JSON (the table allows 131072)
MAX_CHECK_BODY = 160_000  # request bodies of POST /v1/thesis-checks/{id}
MAX_CLAIMS = 30
MAX_NOT_MENTIONED = 10
UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
HASH = r"^[0-9a-f]{64}$"
NOT_FOUND = "thesis not found"

Num = Annotated[float, Field(allow_inf_nan=False)]
Flag = Literal["above", "below"] | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _text(limit: int, min_length: int = 0) -> Any:
    return Field(min_length=min_length, max_length=limit)


class CheckCitation(_Strict):
    """A filing cited for a claim, assumption or point: what the agent's tools returned for
    it (form, filed, company, EDGAR URL), the section and figure the model pointed at, and the
    newest as-of of the tool results that contained it. `verified`: a tool returned it."""
    accession_no: str = Field(pattern=ACCESSION)
    form: str | None = Field(default=None, max_length=20)
    filed: str | None = Field(default=None, pattern=ISO_DATE)
    company_id: str | None = Field(default=None, pattern=CIK)
    url: str | None = Field(default=None, max_length=300, pattern=r"^https://www\.sec\.gov/")
    section_key: str | None = Field(default=None, pattern=SECTION_KEY)
    figure: str | None = Field(default=None, max_length=60)
    figure_status: Literal["verified", "derived", "quoted", "unverified"] | None = None
    as_of: str | None = Field(default=None, pattern=TIMESTAMP)
    verified: bool


def _cited(citations: list[CheckCitation]) -> bool:
    return any(c.verified for c in citations)


class Claim(_Strict):
    quote: str = _text(600, 1)
    quote_found: bool  # the quote appears in the thesis text (the web app highlights it)
    status: Literal["supported", "contradicted", "unverifiable"]
    explanation: str = _text(2000)
    citations: list[CheckCitation] = Field(default=[], max_length=8)
    figures: list[ReportFigure] = Field(default=[], max_length=40)
    downgraded: bool = False  # the model said supported/contradicted without a usable citation

    @model_validator(mode="after")
    def _evidence(self) -> "Claim":
        if self.status != "unverifiable" and not _cited(self.citations):
            raise ValueError("a supported or contradicted claim cites a filing the tools "
                             "returned")
        return self


class Stats(_Strict):
    """models/model.ts HistoryStats (ratios), as services/agent model_stats.py computes it."""
    cagr: Num | None = None
    cagr_years: int = Field(ge=0, le=10)
    avg_margin: Num | None = None
    margin_years: int = Field(ge=0, le=10)
    last_growth: Num | None = None
    last_margin: Num | None = None


class AssumptionYear(_Strict):
    fy: int = Field(ge=1, le=9999)
    growth: Num  # percent, as the user typed it
    margin: Num
    growth_vs_cagr: Num | None = None  # percentage points
    growth_flag: Flag = None
    margin_flag: Flag = None
    commentary: str = _text(1500)
    citations: list[CheckCitation] = Field(default=[], max_length=6)
    figures: list[ReportFigure] = Field(default=[], max_length=20)


class ModelCheck(_Strict):
    ref: str = Field(pattern=r"^model-[1-9]$")
    item_id: str = Field(pattern=UUID_RE)
    name: str = Field(max_length=4 * ITEM_NAME)
    company_id: str = Field(pattern=CIK)
    base_fy: int | None = Field(default=None, ge=1, le=9999)
    stats: Stats | None = None
    margin_min: Num | None = None
    margin_max: Num | None = None
    years: list[AssumptionYear] = Field(default=[], max_length=10)
    error: str | None = Field(default=None, max_length=200)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_name(v, ITEM_NAME)


class NotMentioned(_Strict):
    point: str = _text(1500, 1)
    citations: list[CheckCitation] = Field(min_length=1, max_length=6)
    figures: list[ReportFigure] = Field(default=[], max_length=20)

    @model_validator(mode="after")
    def _evidence(self) -> "NotMentioned":
        if not _cited(self.citations):
            raise ValueError("a point not mentioned cites a filing the tools returned")
        return self


class CheckVerification(_Strict):
    ok: bool = False
    figures: list[ReportFigure] = Field(default=[], max_length=400)
    unverified_accessions: list[Annotated[str, Field(pattern=ACCESSION)]] = Field(
        default=[], max_length=50)
    used_data: bool = False
    freshness: bool = False

    @model_validator(mode="after")
    def _ok(self) -> "CheckVerification":
        """Recomputed, never taken from the request (verify.Verification.ok)."""
        self.ok = (not self.unverified_accessions
                   and all(f.status != "unverified" for f in self.figures)
                   and (self.freshness or not self.used_data))
        return self


class ThesisRef(_Strict):
    item_id: str = Field(pattern=UUID_RE)
    title: str = Field(max_length=4 * ITEM_NAME)
    company_id: str | None = Field(default=None, pattern=CIK)
    text_hash: str = Field(pattern=HASH)
    characters: int = Field(ge=0, le=1_000_000)
    characters_checked: int = Field(ge=0, le=1_000_000)
    truncated: bool


class FilingRef(_Strict):
    accession_no: str = Field(pattern=ACCESSION)
    form: str = Field(min_length=1, max_length=20)
    filed: str = Field(pattern=ISO_DATE)


class FilingBasis(_Strict):
    """Revisit: what a check knew of one company's filings (agent thesis_check.filings_basis)."""
    company_id: str = Field(pattern=CIK)
    latest: FilingRef | None = None  # newest 10-K/10-Q in the SEC index at check time
    evidence: FilingRef | None = None  # newest filing the result's verified citations cite

    @field_validator("latest")
    @classmethod
    def _periodic(cls, v: FilingRef | None) -> FilingRef | None:
        if v is not None and v.form not in ("10-K", "10-Q"):
            raise ValueError("latest is a 10-K or 10-Q")
        return v


class Usage(_Strict):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)


class CheckResult(_Strict):
    version: Literal[1]
    thesis: ThesisRef
    summary: str = _text(3000)
    claims: list[Claim] = Field(default=[], max_length=MAX_CLAIMS)
    assumptions: list[ModelCheck] = Field(default=[], max_length=4)
    not_mentioned: list[NotMentioned] = Field(default=[], max_length=MAX_NOT_MENTIONED)
    notes: list[Annotated[str, Field(max_length=300)]] = Field(default=[], max_length=10)
    citations: list[ReportCitation] = Field(default=[], max_length=200)
    verification: CheckVerification
    as_of: str | None = Field(default=None, pattern=TIMESTAMP)
    checked_at: str = Field(pattern=TIMESTAMP)
    model: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._:-]+$")
    questions: int = Field(ge=1, le=10)
    stop: Literal["end_turn", "max_rounds", "max_tokens", "refusal"]
    rounds: int = Field(ge=0, le=50)
    usage: Usage
    cost_usd: Num | None = Field(default=None, ge=0)
    filings: list[FilingBasis] = Field(default=[], max_length=12)  # Revisit (optional)

    @model_validator(mode="after")
    def _basis(self) -> "CheckResult":
        """Evidence names a filing the result cites as verified for that company; one entry
        per company."""
        if len({f.company_id for f in self.filings}) != len(self.filings):
            raise ValueError("filings lists a company twice")
        cited = {(c.company_id, c.accession_no)
                 for x in (*self.claims, *self.not_mentioned) for c in x.citations if c.verified}
        cited |= {(c.company_id, c.accession_no) for m in self.assumptions for y in m.years
                  for c in y.citations if c.verified}
        for f in self.filings:
            if f.evidence and (f.company_id, f.evidence.accession_no) not in cited:
                raise ValueError("filings evidence is not a verified citation of the check")
        return self


def counts(result: dict) -> dict:
    """What the history list shows of a check, without its result."""
    by = {s: sum(c["status"] == s for c in result["claims"])
          for s in ("supported", "contradicted", "unverifiable")}
    flagged = sum(1 for m in result["assumptions"] for y in m.get("years", [])
                  if y.get("growth_flag") or y.get("margin_flag"))
    return {**by, "flagged_assumptions": flagged, "not_mentioned": len(result["not_mentioned"]),
            "ok": result["verification"]["ok"]}


def validate_check(raw: Any) -> dict:
    """A check's result as stored; StateError (with a safe message) if it doesn't fit."""
    try:
        result = CheckResult.model_validate(raw).model_dump()
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        raise StateError(str(e)) from None
    if len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode()) \
            > MAX_CHECK_BYTES:
        raise StateError(f"check is larger than {MAX_CHECK_BYTES} bytes")
    return result


# --- storage --------------------------------------------------------------------------------

class ThesisChecks(Protocol):
    def add(self, user_id: str, item_id: str, result: dict) -> dict | None: ...
    def list(self, user_id: str, item_id: str) -> list[dict] | None: ...
    def get(self, user_id: str, item_id: str, check_id: str) -> dict | None: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


SUMMARY = ("check_id::text, item_id::text, text_hash, model, cost_usd, counts, created_at,"
           # Revisit: what the web app compares with the filing index (absent before it: [])
           " result #>> '{thesis,company_id}' AS company_id,"
           " result ->> 'checked_at' AS checked_at,"
           " COALESCE(result -> 'filings', '[]'::jsonb) AS filings")


class AppThesisChecks:
    """Each method is one transaction as `user_id` (app.user_id for the forced RLS), filtered
    on it; None when the item isn't the caller's thesis."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str, lock: bool = False) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            if lock:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("thesis-checks:" + user_id,))
            yield cur

    @staticmethod
    def _is_thesis(cur: psycopg.Cursor, user_id: str, item_id: str) -> bool:
        cur.execute("SELECT 1 FROM workspace_items WHERE item_id = %s AND user_id = %s"
                    " AND kind = 'thesis'", (item_id, user_id))
        return cur.fetchone() is not None

    def add(self, user_id: str, item_id: str, result: dict) -> dict | None:
        with self._tx(user_id, lock=True) as cur:
            cur.execute("INSERT INTO thesis_checks (item_id, user_id, text_hash, model,"
                        "                           cost_usd, counts, result, created_at)"
                        " SELECT i.item_id, i.user_id, %s, %s, %s, %s, %s, statement_timestamp()"
                        " FROM workspace_items i"
                        " WHERE i.item_id = %s AND i.user_id = %s AND i.kind = 'thesis'"
                        f" RETURNING {SUMMARY}",
                        (result["thesis"]["text_hash"], result["model"], result["cost_usd"],
                         Jsonb(counts(result)), Jsonb(result), item_id, user_id))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("DELETE FROM thesis_checks WHERE user_id = %s AND item_id = %s"
                        " AND check_id IN (SELECT check_id FROM thesis_checks"
                        "  WHERE user_id = %s AND item_id = %s"
                        "  ORDER BY created_at DESC, check_id OFFSET %s)",
                        (user_id, item_id, user_id, item_id, MAX_CHECKS))
            return row

    def list(self, user_id: str, item_id: str) -> list[dict] | None:
        with self._tx(user_id) as cur:
            if not self._is_thesis(cur, user_id, item_id):
                return None
            cur.execute(f"SELECT {SUMMARY} FROM thesis_checks"
                        " WHERE item_id = %s AND user_id = %s"
                        " ORDER BY created_at DESC, check_id", (item_id, user_id))
            return cur.fetchall()

    def get(self, user_id: str, item_id: str, check_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {SUMMARY}, result FROM thesis_checks"
                        " WHERE check_id = %s AND item_id = %s AND user_id = %s",
                        (check_id, item_id, user_id))
            return cur.fetchone()


# --- routes ---------------------------------------------------------------------------------

class NewCheck(_Strict):
    result: dict[str, Any]


ItemId = Annotated[UUID, Path(description="a thesis's item_id")]
CheckId = Annotated[UUID, Path(description="a check_id")]


def add_thesis_check_routes(app: FastAPI, *, checks: ThesisChecks | None,
                            current_user: Callable[..., str]) -> None:
    User = Annotated[str, Depends(current_user)]

    def store() -> ThesisChecks:
        if checks is None:
            raise HTTPException(503, "thesis checks unavailable")
        return checks

    @app.post("/v1/thesis-checks/{item_id}", status_code=201, response_model=dict)
    def add_check(user_id: User, item_id: ItemId, body: NewCheck) -> dict:
        """Stores a check agent-api ran on the caller's thesis (agent-api only: nginx keeps it
        off the public site). Validated against CheckResult; the thesis must be the caller's."""
        try:
            result = validate_check(body.result)
        except StateError as e:
            raise HTTPException(422, f"invalid check: {e}") from None
        if result["thesis"]["item_id"] != str(item_id):
            raise HTTPException(422, "invalid check: it is for another thesis")
        row = store().add(user_id, str(item_id), result)
        if row is None:
            raise HTTPException(404, NOT_FOUND)
        return row

    @app.get("/v1/workspace-items/{item_id}/checks", response_model=dict)
    def list_checks(user_id: User, item_id: ItemId) -> dict:
        """The thesis's checks, newest first: when, the text hash it checked, model, cost and
        counts (no results)."""
        rows = store().list(user_id, str(item_id))
        if rows is None:
            raise HTTPException(404, NOT_FOUND)
        return {"item_id": str(item_id), "checks": rows}

    @app.get("/v1/workspace-items/{item_id}/checks/{check_id}", response_model=dict)
    def get_check(user_id: User, item_id: ItemId, check_id: CheckId) -> dict:
        row = store().get(user_id, str(item_id), str(check_id))
        if row is None:
            raise HTTPException(404, "check not found")
        return row
