"""Watchlists in the app database (db/app: `watchlists`). Preferences only: a watchlist entry
never grants access to anything (root CLAUDE.md), so routes read data for watched companies
exactly as for any other company. The user is always the JWT `sub`: every statement filters on
it and runs with app.user_id set to it (owner-only, forced row-level security: app 045)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

MAX_ENTRIES = 500


class UnknownUser(Exception):
    """The caller has no row in app.users, so nothing can be saved for them."""


class Watchlists(Protocol):
    def list(self, user_id: str) -> list[dict]: ...  # company_id, added_at
    def add(self, user_id: str, company_id: str) -> bool: ...  # False: already there
    def remove(self, user_id: str, company_id: str) -> bool: ...  # False: wasn't there
    def count(self, user_id: str) -> int: ...


class Pool(Protocol):
    def connection(self) -> Any: ...


class AppWatchlists:
    """Each method is one app-DB transaction as `user_id` (app.user_id for the forced RLS), and
    every statement filters on it as well."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @contextmanager
    def _tx(self, user_id: str) -> Iterator[psycopg.Cursor]:
        with self._pool.connection() as conn, conn.transaction(), \
                conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))
            yield cur

    def list(self, user_id: str) -> list[dict]:
        with self._tx(user_id) as cur:
            cur.execute("SELECT company_id, added_at FROM watchlists WHERE user_id = %s"
                        " ORDER BY added_at, company_id", (user_id,))
            return cur.fetchall()

    def count(self, user_id: str) -> int:
        with self._tx(user_id) as cur:
            cur.execute("SELECT count(*) AS n FROM watchlists WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row["n"] if row else 0

    def add(self, user_id: str, company_id: str) -> bool:
        try:
            with self._tx(user_id) as cur:
                cur.execute("INSERT INTO watchlists (user_id, company_id) VALUES (%s, %s)"
                            " ON CONFLICT DO NOTHING", (user_id, company_id))
                return cur.rowcount == 1
        except psycopg.errors.ForeignKeyViolation:
            raise UnknownUser(user_id) from None

    def remove(self, user_id: str, company_id: str) -> bool:
        with self._tx(user_id) as cur:
            cur.execute("DELETE FROM watchlists WHERE user_id = %s AND company_id = %s",
                        (user_id, company_id))
            return cur.rowcount == 1
