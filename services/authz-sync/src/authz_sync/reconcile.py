"""Apply the difference between desired and current permissions."""

import logging

import psycopg
from psycopg import sql

from authz_sync.state import Desired
from tributary_authz import FgaClient

log = logging.getLogger("authz_sync")

# Postgres tables authz-sync owns: name → key columns (the whole row but synced_at).
TABLES: dict[str, tuple[str, ...]] = {
    "user_access": ("user_id", "company_id", "tier"),
    "user_tiers": ("user_id", "tier"),
    "public_access": ("company_id", "tier"),
}
COUNT_KEYS = {"user_access": "access", "user_tiers": "tiers", "public_access": "public"}


class Reconciler:
    def __init__(self, fga: FgaClient, conn: psycopg.Connection) -> None:
        self._fga = fga
        self._conn = conn
        self._tuples = fga.read_all()  # single writer: track what's applied after this

    def apply(self, desired: Desired) -> dict[str, int]:
        tuples = desired.tuples
        add_t, del_t = tuples - self._tuples, self._tuples - tuples
        if add_t or del_t:
            try:
                self._fga.write(writes=add_t, deletes=del_t)
            except BaseException:
                self._tuples = self._fga.read_all()  # resync: a chunk may have failed midway
                raise
            # Single writer, so the store now holds exactly this. Not re-read: with the
            # catalog's ~18,000 tuples a full read is ~180 requests on every change.
            self._tuples = (self._tuples - del_t) | add_t
        counts = {"tuples_added": len(add_t), "tuples_deleted": len(del_t)}
        rows = {"user_access": desired.access, "user_tiers": desired.user_tiers,
                "public_access": desired.public_access}
        # one transaction: RLS never sees a half-applied change across the three tables
        with self._conn.transaction(), self._conn.cursor() as cur:
            for table, want in rows.items():
                added, deleted = _apply_rows(cur, table, TABLES[table], want)
                counts[f"{COUNT_KEYS[table]}_added"] = added
                counts[f"{COUNT_KEYS[table]}_deleted"] = deleted
        return counts


def _apply_rows(cur: psycopg.Cursor, table: str, cols: tuple[str, ...],
                want: set[tuple[str, ...]]) -> tuple[int, int]:
    """Make authz.<table> hold exactly `want`, deletes first. The EXCLUSIVE lock serializes
    writers (a second authz-sync during a rollout) while plain SELECTs, i.e. RLS, go on; unlike
    SELECT ... FOR UPDATE it dirties no rows, which matters for public_access's ~12,000."""
    name = sql.Identifier("authz", table)
    columns = sql.SQL(", ").join(map(sql.Identifier, cols))
    cur.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(name))
    cur.execute(sql.SQL("SELECT {} FROM {}").format(columns, name))
    current = set(cur.fetchall())
    add, delete = want - current, current - want
    # one statement each, whatever the size: column arrays, unnested in step
    arrays = sql.SQL(", ").join(sql.SQL("%s::text[]") for _ in cols)
    if delete:
        cur.execute(sql.SQL("DELETE FROM {} WHERE ({}) IN (SELECT * FROM unnest({}))").format(
            name, columns, arrays), _columns(delete, len(cols)))
    if add:
        cur.execute(sql.SQL("INSERT INTO {} ({}) SELECT * FROM unnest({})").format(
            name, columns, arrays), _columns(add, len(cols)))
    return len(add), len(delete)


def _columns(rows: set[tuple[str, ...]], n: int) -> list[list[str]]:
    ordered = sorted(rows)
    return [[r[i] for r in ordered] for i in range(n)]
