"""Customizable views (app migration 006): each user's Home arrangement and the column sets of
the screener and the watchlist, saved as preferences; no saved row means the default.

Owner-only, like saved work: the routes take the user only from the JWT `sub`, every statement
filters on it, and the table's forced row-level security admits only `app.user_id`'s rows.
A preference grants nothing: it is a list of Home card ids or screener column keys, each
checked against a fixed catalog here (`HOME_CARDS`, the screener's `COLUMNS`), and the pages
still read every value through the permission-checked routes (an ungranted company's values
stay masked whatever columns are chosen).

Routes: `GET /v1/views` (every view: saved or null, the defaults and the options to choose
from), `PUT /v1/views/{view}` (save), `DELETE /v1/views/{view}` (reset to the default)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal, Protocol

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from query_api.screener import COLUMNS, column_info

View = Literal["home", "screener", "watchlist"]
VIEWS: tuple[str, ...] = ("home", "screener", "watchlist")
# Home's cards (services/web src/views/model.ts HOME_CARDS), in their default order
HOME_CARDS: tuple[str, ...] = ("status", "recent", "watchlist", "filings", "expected", "notes")
MAX_SCREENER_COLUMNS = 12
MAX_WATCHLIST_COLUMNS = 8
# the web app's defaults (companies/screen.ts SCREEN_COLUMNS, api.ts WATCHLIST_COLUMNS)
DEFAULT_SCREENER = ["revenue_ttm", "revenue_growth_ttm", "gross_margin_ttm",
                    "operating_margin_ttm", "free_cash_flow_ttm", "net_debt_q",
                    "debt_to_equity_q"]
DEFAULT_WATCHLIST = ["revenue_ttm", "revenue_growth_ttm", "operating_margin_ttm",
                     "free_cash_flow_ttm", "net_debt_q"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HomeCard(_Strict):
    id: Literal["status", "recent", "watchlist", "filings", "expected", "notes"]
    hidden: bool = False


class HomeView(_Strict):
    """Cards in the order shown; a card left out is shown after the listed ones (a new card
    appears for everyone without a migration of saved arrangements)."""
    cards: list[HomeCard] = Field(min_length=1, max_length=len(HOME_CARDS))

    @field_validator("cards")
    @classmethod
    def _distinct(cls, v: list[HomeCard]) -> list[HomeCard]:
        if len({c.id for c in v}) != len(v):
            raise ValueError("cards repeat")
        return v


def _columns(limit: int) -> Callable[[list[str]], list[str]]:
    def check(v: list[str]) -> list[str]:
        if not 1 <= len(v) <= limit:
            raise ValueError(f"choose 1 to {limit} columns")
        if len(set(v)) != len(v):
            raise ValueError("columns repeat")
        unknown = [c for c in v if c not in COLUMNS]
        if unknown:
            raise ValueError(f"unknown columns: {', '.join(unknown[:5])}; "
                             "GET /v1/screener/columns lists them")
        return v
    return check


class ScreenerView(_Strict):
    columns: list[Annotated[str, Field(max_length=64)]]

    @field_validator("columns")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        return _columns(MAX_SCREENER_COLUMNS)(v)


class WatchlistView(_Strict):
    columns: list[Annotated[str, Field(max_length=64)]]

    @field_validator("columns")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        return _columns(MAX_WATCHLIST_COLUMNS)(v)


MODELS: dict[str, type[BaseModel]] = {"home": HomeView, "screener": ScreenerView,
                                      "watchlist": WatchlistView}
DEFAULTS: dict[str, dict] = {
    "home": {"cards": [{"id": c, "hidden": False} for c in HOME_CARDS]},
    "screener": {"columns": DEFAULT_SCREENER},
    "watchlist": {"columns": DEFAULT_WATCHLIST},
}


class ViewError(ValueError):
    """A preference that doesn't fit its view's schema; the message is safe to return."""


def validate_view(view: str, raw: Any) -> dict:
    model = MODELS.get(view)
    if model is None:
        raise ViewError(f"unknown view; one of {', '.join(VIEWS)}")
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError as e:
        raise ViewError("; ".join(f"{'.'.join(map(str, x['loc'])) or 'state'}: {x['msg']}"
                                  for x in e.errors()[:5])) from None


# --- storage --------------------------------------------------------------------------------

class UnknownUser(Exception):
    """No app.users row for the caller."""


class Views(Protocol):
    def all(self, user_id: str) -> dict[str, dict]: ...
    def put(self, user_id: str, view: str, state: dict) -> None: ...
    def delete(self, user_id: str, view: str) -> bool: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


class AppViews:
    """Each method is one transaction as `user_id` (app.user_id for the row-level security),
    filtering on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    def all(self, user_id: str) -> dict[str, dict]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT view, state FROM user_views WHERE user_id = %s", (user_id,))
            return {r["view"]: r["state"] for r in cur.fetchall()}

    def put(self, user_id: str, view: str, state: dict) -> None:
        try:
            with self._tx(user_id) as cur:
                cur.execute("INSERT INTO user_views (user_id, view, state, updated_at)"
                            " VALUES (%s, %s, %s, statement_timestamp())"
                            " ON CONFLICT (user_id, view) DO UPDATE"
                            " SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at",
                            (user_id, view, Jsonb(state)))
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def delete(self, user_id: str, view: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM user_views WHERE user_id = %s AND view = %s",
                        (user_id, view))
            return cur.rowcount == 1


# --- routes ---------------------------------------------------------------------------------

class ViewBody(_Strict):
    state: dict[str, Any]


def add_view_routes(app: FastAPI, *, views: Views | None,
                    current_user: Callable[..., str]) -> None:
    User = Annotated[str, Depends(current_user)]

    def store() -> Views:
        if views is None:
            raise HTTPException(503, "preferences unavailable")
        return views

    @app.get("/v1/views", response_model=dict)
    def get_views(user_id: User) -> dict:
        """Every view: the saved preference (null: the default), the defaults, and what can be
        chosen (Home's cards, the screener's columns)."""
        saved = store().all(user_id)
        out: dict[str, Any] = {}
        for view in VIEWS:
            try:  # a row from an older catalog that no longer validates reads as the default
                out[view] = validate_view(view, saved[view]) if view in saved else None
            except ViewError:
                out[view] = None
        return {"views": out, "defaults": DEFAULTS,
                "options": {"home_cards": list(HOME_CARDS),
                            "columns": column_info(sorted(COLUMNS)),
                            "max_columns": {"screener": MAX_SCREENER_COLUMNS,
                                            "watchlist": MAX_WATCHLIST_COLUMNS}}}

    @app.put("/v1/views/{view}", response_model=dict)
    def put_view(user_id: User, view: View, body: ViewBody) -> dict:
        try:
            state = validate_view(view, body.state)
        except ViewError as e:
            raise HTTPException(422, f"invalid {view} view: {e}") from None
        try:
            store().put(user_id, view, state)
        except UnknownUser:
            raise HTTPException(409, "no account to save preferences to") from None
        return {"view": view, "state": state}

    @app.delete("/v1/views/{view}", response_model=dict)
    def reset_view(user_id: User, view: View) -> dict:
        """Back to the default (idempotent)."""
        store().delete(user_id, view)
        return {"view": view, "state": DEFAULTS[view], "reset": True}
