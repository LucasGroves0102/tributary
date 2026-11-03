"""Saved work in the app database (db/app migrations 004, 005, 009, 012): workspaces (named
folders, or analysis projects bound to one company) of saved items (a chart builder state, a
comps set, a screener screen, a thesis the user writes, a revenue model; reports the agent
writes), and the filings a user last opened, for Home's "Continue where you left off".

Owner-only: every statement is filtered on the JWT `sub` the route passes in, and the tables'
row-level security (FORCEd, keyed on `app.user_id`, set per transaction here) admits only that
user's rows, so a missed WHERE still can't reach another user's work. Another user's workspace
or item and one that doesn't exist get the same 404. Ids are random UUIDs.

A saved item grants nothing: it holds company ids and settings, validated per kind below, and
the web app reads the data through the Query API like any other screen. Sharing (OpenFGA
owner/viewer + CDC + authz-sync) is a later step (README, "Workspaces")."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_WORKSPACES = 50
MAX_ITEMS_PER_WORKSPACE = 100
MAX_ITEMS = 500  # per user, across workspaces
MAX_RECENT_FILINGS = 20
MAX_STATE_BYTES = 16_000  # an item's state as compact JSON (the table allows 16384)
MAX_BODY_BYTES = 32_768  # request bodies of the saved-work routes
MAX_ITEM_BODY = 160_000  # ...but an item's create/save body may carry a thesis
MAX_THESIS_MARKDOWN = 100_000  # characters
MAX_THESIS_BYTES = 120_000  # a thesis state as compact UTF-8 JSON (the table allows 131072)
MAX_MODEL_YEARS = 10
MAX_NOTE = 500
MAX_PROJECT_PEERS = 20  # like a comps set (CompsState.peers)
MAX_PROJECT_NOTES = 4_000  # "How it makes money", characters
MAX_PROJECT_BYTES = 16_000  # a project as compact JSON (the column allows 16384)
# the guided steps of an analysis project, in order (web analysis/project.ts STEPS)
STEPS = ("business", "numbers", "changes", "peers", "model", "thesis", "monitor")
Step = Literal["business", "numbers", "changes", "peers", "model", "thesis", "monitor"]
WORKSPACE_NAME = 100
ITEM_NAME = 120
# what users save (009 adds thesis and model); reports (005) only the agent writes
KINDS = ("chart", "comps", "screen", "thesis", "model")
Kind = Literal["chart", "comps", "screen", "thesis", "model"]
REPORT = "report"
LARGE = ("report", "thesis")  # kinds whose state the workspace listing leaves out

CIK = r"^\d{10}$"
ISO_DATE = r"^\d{4}-\d{2}-\d{2}$"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")  # text keeps newlines and tabs


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def clean_name(value: str, limit: int) -> str:
    """Names are shown back as text: trimmed, 1..limit characters, no control characters."""
    name = value.strip()
    if not name:
        raise ValueError("name is empty")
    if len(name) > limit:
        raise ValueError(f"name is longer than {limit} characters")
    if _CONTROL.search(name):
        raise ValueError("name contains control characters")
    return name


# --- item states: the web app's own state shapes, so it restores them as they are ---------

SEGMENT_MEMBER = r"^[A-Za-z0-9_.:-]{1,200}$"  # an XBRL member QName (chart/segments.ts MEMBER)
MAX_SEGMENT_MEMBERS = 20
SEGMENT_TRANSFORMS = ("q", "ttm", "yoy", "contrib")
# price layers (web chart/prices.ts): quarter-end close, YoY and indexed over adjusted closes
PRICE_TRANSFORMS = ("q", "yoy", "idx")


class ChartLayer(_Strict):
    """chart/model.ts Layer, without its id. A segment layer (the Revenue view's charts) sums
    members of one axis of /v1/companies/{id}/segments; it holds ids and names only, like every
    layer, and the web app reads its values through the permission-checked route. A price
    layer ("Connect your data") names a company only; the web app reads the viewer's OWN
    prices (/v1/companies/{id}/prices), so a saved or embedded price layer never carries or
    shares prices."""
    kind: Literal["metric", "formula", "segment", "price"]
    companyId: str | None = Field(default=None, pattern=CIK)
    metric: str | None = Field(default=None, pattern=r"^[a-z_]{1,64}$")
    formula: str | None = Field(default=None, max_length=500)
    defaultCompany: str | None = Field(default=None, pattern=CIK)
    segmentAxis: Literal["business_segment", "geography", "product_service"] | None = None
    members: list[Annotated[str, Field(pattern=SEGMENT_MEMBER)]] | None = Field(
        default=None, min_length=1, max_length=MAX_SEGMENT_MEMBERS)
    transform: Literal["q", "ttm", "yoy", "qoq", "idx", "ps", "contrib"] = "ttm"
    type: Literal["line", "bar", "area", "sbar", "sarea"] = "line"
    axis: Literal["L", "R"] = "L"
    hidden: bool = False
    name: str | None = Field(default=None, max_length=80)  # legend name
    color: int | None = Field(default=None, ge=0, le=7)    # tokens.css --series-N

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None or not v.strip() else clean_name(v, 80)

    @model_validator(mode="after")
    def _complete(self) -> ChartLayer:
        if self.kind == "segment":
            if (not self.companyId or not self.metric or not self.segmentAxis or not self.members
                    or self.formula is not None or self.defaultCompany is not None):
                raise ValueError("a segment layer has companyId, metric, segmentAxis and "
                                 "members, and no formula")
            if self.transform not in SEGMENT_TRANSFORMS:
                raise ValueError(f"a segment layer's transform is one of {SEGMENT_TRANSFORMS}")
            if len(set(self.members)) != len(self.members):
                raise ValueError("a segment layer lists a member twice")
            return self
        if self.kind == "price":
            if (not self.companyId or self.metric is not None or self.formula is not None
                    or self.defaultCompany is not None or self.segmentAxis is not None
                    or self.members is not None):
                raise ValueError("a price layer has companyId only")
            if self.transform not in PRICE_TRANSFORMS:
                raise ValueError(f"a price layer's transform is one of {PRICE_TRANSFORMS}")
            return self
        if self.segmentAxis is not None or self.members is not None or self.transform == "contrib":
            raise ValueError("segmentAxis, members and contrib are for segment layers")
        if self.kind == "metric":
            if not self.companyId or not self.metric or self.formula is not None:
                raise ValueError("a metric layer has companyId and metric, and no formula")
        elif self.formula is None or self.metric is not None or self.companyId is not None:
            raise ValueError("a formula layer has formula (and maybe defaultCompany) only")
        return self


class ChartState(_Strict):
    """chart/model.ts ChartState, without nextId."""
    title: str = Field(default="", max_length=200)
    subtitle: str | None = Field(default=None, max_length=200)  # deck output
    range: Literal[1, 2, 3, 5, 10, "max", "custom"] = 3
    # range "custom": earliest and latest period end (either may be open)
    start: str | None = Field(default=None, pattern=ISO_DATE)
    end: str | None = Field(default=None, pattern=ISO_DATE)
    frequency: Literal["quarterly", "annual"] | None = None  # unset: quarterly
    align: Literal["calendar", "fiscal"] = "calendar"
    layers: list[ChartLayer] = Field(min_length=1, max_length=12)
    markers: bool = False  # filing markers on the time axis
    deck: bool = False  # deck output: light background, subtitle, source footnote

    @field_validator("subtitle")
    @classmethod
    def _subtitle(cls, v: str | None) -> str | None:
        return None if v is None or not v.strip() else clean_name(v, 200)


CompareColumn = Literal["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm",
                        "operating_margin_ttm", "net_margin_ttm", "free_cash_flow_ttm",
                        "fcf_margin_ttm", "net_debt_q", "debt_to_equity_q", "roe_ttm",
                        "current_ratio_q"]


class CompsState(_Strict):
    """compare/state.ts CompareState: a target, its peers and the columns (unset: defaults)."""
    target: str = Field(pattern=CIK)
    peers: list[Annotated[str, Field(pattern=CIK)]] = Field(default=[], max_length=20)
    cols: list[CompareColumn] | None = Field(default=None, min_length=1, max_length=11)

    @model_validator(mode="after")
    def _distinct(self) -> CompsState:
        if len(set(self.peers)) != len(self.peers) or self.target in self.peers:
            raise ValueError("peers repeat, or include the target")
        if self.cols is not None and len(set(self.cols)) != len(self.cols):
            raise ValueError("columns repeat")
        return self


SCREEN_SORTS = ("name", "sic", "fiscal_year_end", "revenue_ttm", "revenue_growth_ttm",
                "gross_margin_ttm", "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q",
                "debt_to_equity_q")


class ScreenState(_Strict):
    """companies/screen.ts ScreenFilters (without the page): filters and sort."""
    q: str | None = Field(default=None, min_length=1, max_length=100)
    industry: str | None = Field(default=None, min_length=1, max_length=100)
    sic: str | None = Field(default=None, pattern=r"^\d{4}$")
    band: Literal["lt100m", "100m-1b", "1b-10b", "10b-100b", "gte100b"] | None = None
    growth: float | None = Field(default=None, ge=-1000, le=100000)
    margin: float | None = Field(default=None, ge=-1000, le=100)
    fye: int | None = Field(default=None, ge=1, le=12)
    exchange: str | None = Field(default=None, min_length=1, max_length=20)
    filer: Literal["large_accelerated", "accelerated", "non_accelerated"] | None = None
    mine: bool | None = None
    sort: str | None = Field(default=None, pattern=rf"^-?({'|'.join(SCREEN_SORTS)})$")


def clean_text(value: str, what: str) -> str:
    """Multi-line user text: newlines and tabs, no other control characters."""
    if _TEXT_CONTROL.search(value):
        raise ValueError(f"{what} contains control characters")
    return value


class ThesisState(_Strict):
    """thesis/model.ts Thesis: the user's Markdown about a company (optional). The item's name
    is its title. Lines `[[embed:<item id>]]` draw an item saved in the same workspace; the web
    app resolves them against that workspace and drops anything else, so they aren't checked
    here (an item may be deleted after it was embedded)."""
    companyId: str | None = Field(default=None, pattern=CIK)
    markdown: str = Field(default="", max_length=MAX_THESIS_MARKDOWN)

    @field_validator("markdown")
    @classmethod
    def _text(cls, v: str) -> str:
        return clean_text(v, "markdown")


class ModelYear(_Strict):
    """One forecast year's assumptions, in percent: revenue growth over the year before, and
    operating margin (operating income = revenue x margin)."""
    growth: float = Field(ge=-100, le=1000)
    margin: float = Field(ge=-1000, le=100)
    note: str | None = Field(default=None, max_length=MAX_NOTE)

    @field_validator("note")
    @classmethod
    def _note(cls, v: str | None) -> str | None:
        return None if v is None or not v.strip() else clean_text(v.strip(), "note")


class Scenarios(_Strict):
    """The bear and bull cases beside the base one: the same forecast years, other numbers."""
    bear: list[ModelYear] | None = Field(default=None, min_length=1, max_length=MAX_MODEL_YEARS)
    bull: list[ModelYear] | None = Field(default=None, min_length=1, max_length=MAX_MODEL_YEARS)


class ModelState(_Strict):
    """models/model.ts ModelState: a company, how many fiscal years of history to show, and one
    set of assumptions per forecast year (the first is the year after the latest reported one).
    The history itself is never stored: the web app reads it through the Query API.

    `assumptions` is the BASE scenario (a model saved before scenarios is base only, unchanged);
    `scenarios` optionally adds bear and bull assumptions for the same years. The thesis check
    reads the base case (agent-api model_stats.scenario_assumptions)."""
    companyId: str = Field(pattern=CIK)
    historyYears: int = Field(default=8, ge=3, le=10)
    assumptions: list[ModelYear] = Field(min_length=1, max_length=MAX_MODEL_YEARS)
    scenarios: Scenarios | None = None

    @model_validator(mode="after")
    def _same_years(self) -> ModelState:
        if self.scenarios is None:
            return self
        for case in (self.scenarios.bear, self.scenarios.bull):
            if case is not None and len(case) != len(self.assumptions):
                raise ValueError("bear and bull have as many forecast years as the base case")
        if self.scenarios.bear is None and self.scenarios.bull is None:
            self.scenarios = None
        return self


class ProjectState(_Strict):
    """An analysis project (app DB 012, `workspaces.project`; web analysis/project.ts): the
    company it studies, its peers, the user's "How it makes money" notes and the guided steps
    marked done. Company ids and the user's own text only: it grants nothing."""
    companyId: str = Field(pattern=CIK)
    peers: list[Annotated[str, Field(pattern=CIK)]] = Field(default=[],
                                                            max_length=MAX_PROJECT_PEERS)
    notes: str = Field(default="", max_length=MAX_PROJECT_NOTES)
    done: list[Step] = Field(default=[], max_length=len(STEPS))

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str) -> str:
        return clean_text(v, "notes")

    @model_validator(mode="after")
    def _distinct(self) -> ProjectState:
        if len(set(self.peers)) != len(self.peers) or self.companyId in self.peers:
            raise ValueError("peers repeat, or include the company")
        if len(set(self.done)) != len(self.done):
            raise ValueError("a step is listed twice")
        return self


STATES: dict[str, type[BaseModel]] = {"chart": ChartState, "comps": CompsState,
                                      "screen": ScreenState, "thesis": ThesisState,
                                      "model": ModelState}


class StateError(ValueError):
    """An item state that doesn't fit its kind's schema or the size cap."""


def validate_state(kind: str, raw: Any) -> dict:
    """The state as stored: validated against its kind, defaults dropped where unset."""
    model = STATES.get(kind)
    if model is None:
        raise StateError(f"unknown kind; one of {', '.join(KINDS)}")
    try:
        state = model.model_validate(raw).model_dump(exclude_none=True)
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        raise StateError(str(e)) from None
    cap = MAX_THESIS_BYTES if kind == "thesis" else MAX_STATE_BYTES
    if len(json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode()) > cap:
        raise StateError(f"state is larger than {cap} bytes")
    return state


def validate_project(raw: Any) -> dict:
    """An analysis project as stored (ProjectState), or StateError."""
    try:
        project = ProjectState.model_validate(raw).model_dump()
    except ValueError as e:  # pydantic's ValidationError is a ValueError
        raise StateError(str(e)) from None
    if len(json.dumps(project, separators=(",", ":"), ensure_ascii=False).encode()) \
            > MAX_PROJECT_BYTES:
        raise StateError(f"project is larger than {MAX_PROJECT_BYTES} bytes")
    return project


def starter(name: str, items: list[tuple[str, str, dict]]) -> dict:
    """A workspace to seed (demo sandboxes): {name, items: [(kind, name, state)]}, each item
    validated like any saved one."""
    return {"name": clean_name(name, WORKSPACE_NAME),
            "items": [(k, clean_name(n, ITEM_NAME), validate_state(k, s)) for k, n, s in items]}


# --- storage --------------------------------------------------------------------------------

class UnknownUser(Exception):
    """The caller has no row in app.users, so nothing can be saved for them."""


class LimitReached(Exception):
    """A per-user or per-workspace cap; the message says which."""


class NotAProject(Exception):
    """A project was sent for a plain workspace (a folder doesn't become a project)."""


class Workspaces(Protocol):
    def list(self, user_id: str) -> list[dict]: ...
    def create(self, user_id: str, name: str, project: dict | None = None) -> dict: ...
    def rename(self, user_id: str, workspace_id: str, name: str) -> dict | None: ...
    def update(self, user_id: str, workspace_id: str, name: str | None,
               project: dict | None) -> dict | None: ...
    def delete(self, user_id: str, workspace_id: str) -> bool: ...
    def duplicate(self, user_id: str, workspace_id: str, name: str | None) -> dict | None: ...
    def add_item(self, user_id: str, workspace_id: str, kind: str, name: str,
                 state: dict) -> dict | None: ...
    def get(self, user_id: str, workspace_id: str) -> dict | None: ...
    def item(self, user_id: str, item_id: str) -> dict | None: ...
    def update_report(self, user_id: str, workspace_id: str, item_id: str,
                      state: dict) -> dict | None: ...
    def update_item(self, user_id: str, item_id: str, name: str | None,
                    state: dict | None) -> dict | None: ...
    def duplicate_item(self, user_id: str, item_id: str, workspace_id: str | None,
                       name: str | None) -> dict | None: ...
    def delete_item(self, user_id: str, item_id: str) -> bool: ...
    def mark_opened(self, user_id: str, item_id: str) -> datetime | None: ...
    def recent(self, user_id: str, limit: int) -> list[dict]: ...
    def read_filing(self, user_id: str, company_id: str, accession_no: str,
                    section_key: str | None) -> None: ...
    def recent_filings(self, user_id: str, limit: int) -> list[dict]: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


ITEM_SUMMARY = ("i.item_id::text, i.workspace_id::text, i.kind, i.name, i.created_at,"
                " i.updated_at, i.opened_at")
WORKSPACE_COLUMNS = "w.workspace_id::text, w.name, w.created_at, w.updated_at, w.project"


def copy_name(name: str, limit: int) -> str:
    suffix = " (copy)"
    return name[:limit - len(suffix)].rstrip() + suffix


class AppWorkspaces:
    """Every method runs in one transaction as `user_id` (app.user_id for RLS) and filters on
    it; writes that count against a cap take a per-user advisory lock first."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str, lock: bool = False) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            if lock:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                            ("workspaces:" + user_id,))
            yield cur

    def list(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {WORKSPACE_COLUMNS} FROM workspaces w WHERE w.user_id = %s"
                        " ORDER BY w.created_at, w.workspace_id", (user_id,))
            out = [{**w, "items": []} for w in cur.fetchall()]
            cur.execute(f"SELECT {ITEM_SUMMARY} FROM workspace_items i WHERE i.user_id = %s"
                        " ORDER BY i.created_at, i.item_id", (user_id,))
            by_id = {w["workspace_id"]: w for w in out}
            for item in cur.fetchall():
                if item["workspace_id"] in by_id:
                    by_id[item["workspace_id"]]["items"].append(item)
            return out

    def create(self, user_id: str, name: str, project: dict | None = None) -> dict:
        """A workspace; with `project` (validated), an analysis project."""
        try:
            with self._tx(user_id, lock=True) as cur:
                self._room(cur, user_id, workspaces=1)
                cur.execute("INSERT INTO workspaces (user_id, name, project, created_at,"
                            "                        updated_at)"
                            " VALUES (%s, %s, %s, statement_timestamp(), statement_timestamp())"
                            f" RETURNING {WORKSPACE_COLUMNS.replace('w.', '')}",
                            (user_id, name, _jsonb(project) if project is not None else None))
                return {**_one(cur), "items": []}
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def rename(self, user_id: str, workspace_id: str, name: str) -> dict | None:
        return self.update(user_id, workspace_id, name, None)

    def update(self, user_id: str, workspace_id: str, name: str | None,
               project: dict | None) -> dict | None:
        """Renames and/or replaces a project's state (None: unchanged). NotAProject when a
        project is sent for a plain workspace: folders stay folders."""
        with self._tx(user_id) as cur:
            if project is not None:
                cur.execute("SELECT project IS NOT NULL AS is_project FROM workspaces"
                            " WHERE workspace_id = %s AND user_id = %s",
                            (workspace_id, user_id))
                row = cur.fetchone()
                if row is None:
                    return None
                if not row["is_project"]:
                    raise NotAProject(workspace_id)
            cur.execute("UPDATE workspaces SET name = coalesce(%s, name),"
                        " project = coalesce(%s, project), updated_at = statement_timestamp()"
                        " WHERE workspace_id = %s AND user_id = %s"
                        f" RETURNING {WORKSPACE_COLUMNS.replace('w.', '')}",
                        (name, _jsonb(project) if project is not None else None,
                         workspace_id, user_id))
            return cur.fetchone()

    def delete(self, user_id: str, workspace_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM workspaces WHERE workspace_id = %s AND user_id = %s",
                        (workspace_id, user_id))
            return cur.rowcount == 1

    def duplicate(self, user_id: str, workspace_id: str, name: str | None) -> dict | None:
        with self._tx(user_id, lock=True) as cur:
            cur.execute("SELECT name, project FROM workspaces"
                        " WHERE workspace_id = %s AND user_id = %s", (workspace_id, user_id))
            src = cur.fetchone()
            if src is None:
                return None
            cur.execute("SELECT count(*) AS n FROM workspace_items"
                        " WHERE workspace_id = %s AND user_id = %s", (workspace_id, user_id))
            self._room(cur, user_id, workspaces=1, items=_one(cur)["n"])
            cur.execute("INSERT INTO workspaces (user_id, name, project, created_at,"
                        "                        updated_at)"
                        " VALUES (%s, %s, %s, statement_timestamp(), statement_timestamp())"
                        f" RETURNING {WORKSPACE_COLUMNS.replace('w.', '')}",
                        (user_id, name or copy_name(src["name"], WORKSPACE_NAME),
                         _jsonb(src["project"]) if src["project"] is not None else None))
            new = _one(cur)
            # copies keep their order: the statement's time plus 1 µs per item
            cur.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state,"
                        "                             created_at, updated_at)"
                        " SELECT %s, user_id, kind, name, state, t, t FROM ("
                        "  SELECT *, statement_timestamp() + make_interval(secs =>"
                        "         (row_number() OVER (ORDER BY created_at, item_id))::float8"
                        "         / 1000000) AS t"
                        "  FROM workspace_items WHERE workspace_id = %s AND user_id = %s) src",
                        (new["workspace_id"], workspace_id, user_id))
            cur.execute(f"SELECT {ITEM_SUMMARY} FROM workspace_items i"
                        " WHERE i.workspace_id = %s AND i.user_id = %s"
                        " ORDER BY i.created_at, i.item_id", (new["workspace_id"], user_id))
            return {**new, "items": cur.fetchall()}

    def add_item(self, user_id: str, workspace_id: str, kind: str, name: str,
                 state: dict) -> dict | None:
        with self._tx(user_id, lock=True) as cur:
            if not self._owns(cur, user_id, workspace_id):
                return None
            self._room(cur, user_id, items=1, workspace_id=workspace_id)
            return self._insert_item(cur, user_id, workspace_id, kind, name, state)

    def get(self, user_id: str, workspace_id: str) -> dict | None:
        """One workspace with its items in order, each with its state except reports' and
        theses' (large; fetched one at a time)."""
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {WORKSPACE_COLUMNS} FROM workspaces w"
                        " WHERE w.workspace_id = %s AND w.user_id = %s", (workspace_id, user_id))
            ws = cur.fetchone()
            if ws is None:
                return None
            cur.execute(f"SELECT {ITEM_SUMMARY},"
                        " CASE WHEN i.kind = ANY(%s) THEN NULL ELSE i.state END AS state"
                        " FROM workspace_items i WHERE i.workspace_id = %s AND i.user_id = %s"
                        " ORDER BY i.created_at, i.item_id",
                        (list(LARGE), workspace_id, user_id))
            return {**ws, "items": cur.fetchall()}

    def update_report(self, user_id: str, workspace_id: str, item_id: str,
                      state: dict) -> dict | None:
        """Writes over a report (regenerated); None unless it is a report in that workspace."""
        with self._tx(user_id) as cur:
            cur.execute("UPDATE workspace_items i SET state = %s,"
                        " updated_at = statement_timestamp()"
                        " WHERE i.item_id = %s AND i.user_id = %s AND i.workspace_id = %s"
                        " AND i.kind = 'report'"
                        f" RETURNING {ITEM_SUMMARY}, i.state",
                        (_jsonb(state), item_id, user_id, workspace_id))
            return cur.fetchone()

    def item(self, user_id: str, item_id: str) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {ITEM_SUMMARY}, i.state, w.name AS workspace_name"
                        " FROM workspace_items i JOIN workspaces w USING (workspace_id, user_id)"
                        " WHERE i.item_id = %s AND i.user_id = %s", (item_id, user_id))
            return cur.fetchone()

    def update_item(self, user_id: str, item_id: str, name: str | None,
                    state: dict | None) -> dict | None:
        with self._tx(user_id) as cur:
            cur.execute("UPDATE workspace_items i SET name = coalesce(%s, i.name),"
                        " state = coalesce(%s, i.state), updated_at = statement_timestamp()"
                        " WHERE i.item_id = %s AND i.user_id = %s"
                        f" RETURNING {ITEM_SUMMARY}, i.state",
                        (name, _jsonb(state) if state is not None else None, item_id, user_id))
            return cur.fetchone()

    def duplicate_item(self, user_id: str, item_id: str, workspace_id: str | None,
                       name: str | None) -> dict | None:
        with self._tx(user_id, lock=True) as cur:
            cur.execute("SELECT workspace_id::text, kind, name, state FROM workspace_items"
                        " WHERE item_id = %s AND user_id = %s", (item_id, user_id))
            src = cur.fetchone()
            if src is None:
                return None
            target = workspace_id or src["workspace_id"]
            if not self._owns(cur, user_id, target):
                return None
            self._room(cur, user_id, items=1, workspace_id=target)
            return self._insert_item(cur, user_id, target, src["kind"],
                                     name or copy_name(src["name"], ITEM_NAME), src["state"])

    def delete_item(self, user_id: str, item_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM workspace_items WHERE item_id = %s AND user_id = %s",
                        (item_id, user_id))
            return cur.rowcount == 1

    def mark_opened(self, user_id: str, item_id: str) -> datetime | None:
        with self._tx(user_id) as cur:
            cur.execute("UPDATE workspace_items SET opened_at = clock_timestamp()"
                        " WHERE item_id = %s AND user_id = %s RETURNING opened_at",
                        (item_id, user_id))
            row = cur.fetchone()
            return row["opened_at"] if row else None

    def recent(self, user_id: str, limit: int) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute(f"SELECT {ITEM_SUMMARY}, w.name AS workspace_name"
                        " FROM workspace_items i JOIN workspaces w USING (workspace_id, user_id)"
                        " WHERE i.user_id = %s AND i.opened_at IS NOT NULL"
                        " ORDER BY i.opened_at DESC, i.item_id LIMIT %s", (user_id, limit))
            return cur.fetchall()

    def read_filing(self, user_id: str, company_id: str, accession_no: str,
                    section_key: str | None) -> None:
        try:
            with self._tx(user_id, lock=True) as cur:
                cur.execute("INSERT INTO recent_filings (user_id, company_id, accession_no,"
                            "                            section_key, read_at)"
                            " VALUES (%s, %s, %s, %s, clock_timestamp())"
                            " ON CONFLICT (user_id, company_id, accession_no) DO UPDATE"
                            " SET section_key = coalesce(EXCLUDED.section_key,"
                            "                            recent_filings.section_key),"
                            "     read_at = EXCLUDED.read_at",
                            (user_id, company_id, accession_no, section_key))
                cur.execute("DELETE FROM recent_filings WHERE user_id = %s"
                            " AND (company_id, accession_no) IN"
                            " (SELECT company_id, accession_no FROM recent_filings"
                            "  WHERE user_id = %s ORDER BY read_at DESC, accession_no OFFSET %s)",
                            (user_id, user_id, MAX_RECENT_FILINGS))
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def recent_filings(self, user_id: str, limit: int) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT company_id, accession_no, section_key, read_at"
                        " FROM recent_filings WHERE user_id = %s"
                        " ORDER BY read_at DESC, accession_no LIMIT %s", (user_id, limit))
            return cur.fetchall()

    # -- helpers

    def _owns(self, cur: psycopg.Cursor, user_id: str, workspace_id: str) -> bool:
        cur.execute("SELECT 1 FROM workspaces WHERE workspace_id = %s AND user_id = %s",
                    (workspace_id, user_id))
        return cur.fetchone() is not None

    def _room(self, cur: psycopg.Cursor, user_id: str, *, workspaces: int = 0, items: int = 0,
              workspace_id: str | None = None) -> None:
        cur.execute("SELECT (SELECT count(*) FROM workspaces WHERE user_id = %s) AS workspaces,"
                    " (SELECT count(*) FROM workspace_items WHERE user_id = %s) AS items,"
                    " (SELECT count(*) FROM workspace_items WHERE user_id = %s"
                    "  AND workspace_id = %s) AS here",
                    (user_id, user_id, user_id, workspace_id))
        n = _one(cur)
        if n["workspaces"] + workspaces > MAX_WORKSPACES:
            raise LimitReached(f"you can have at most {MAX_WORKSPACES} workspaces")
        if n["items"] + items > MAX_ITEMS:
            raise LimitReached(f"you can save at most {MAX_ITEMS} items")
        if workspace_id is not None and n["here"] + items > MAX_ITEMS_PER_WORKSPACE:
            raise LimitReached(f"a workspace holds at most {MAX_ITEMS_PER_WORKSPACE} items")

    def _insert_item(self, cur: psycopg.Cursor, user_id: str, workspace_id: str, kind: str,
                     name: str, state: Any) -> dict:
        cur.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state,"
                    "                             created_at, updated_at)"
                    " VALUES (%s, %s, %s, %s, %s, statement_timestamp(), statement_timestamp())"
                    f" RETURNING {ITEM_SUMMARY.replace('i.', '')}, state",
                    (workspace_id, user_id, kind, name, _jsonb(state)))
        item = _one(cur)
        cur.execute("UPDATE workspaces SET updated_at = statement_timestamp()"
                    " WHERE workspace_id = %s AND user_id = %s", (workspace_id, user_id))
        return item


def seed(cur: psycopg.Cursor, user_id: str, workspaces: list[dict]) -> None:
    """Inserts starter workspaces for a new user inside the caller's transaction (sandbox
    creation); sets app.user_id for the row-level security."""
    cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
    for w in workspaces:
        cur.execute("INSERT INTO workspaces (user_id, name, created_at, updated_at)"
                    " VALUES (%s, %s, statement_timestamp(), statement_timestamp())"
                    " RETURNING workspace_id", (user_id, w["name"]))
        row = cur.fetchone()
        assert row is not None
        workspace_id = row[0]
        for kind, name, state in w["items"]:
            cur.execute("INSERT INTO workspace_items (workspace_id, user_id, kind, name, state,"
                        "                             created_at, updated_at)"
                        " VALUES (%s, %s, %s, %s, %s, statement_timestamp(),"
                        "         statement_timestamp())",
                        (workspace_id, user_id, kind, name, _jsonb(state)))


def _jsonb(value: Any) -> Jsonb:
    return Jsonb(value)


def _one(cur: psycopg.Cursor) -> dict:
    row = cur.fetchone()
    assert row is not None
    return row
