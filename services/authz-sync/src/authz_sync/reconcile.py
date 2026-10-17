"""Apply the difference between desired and current permissions."""

import logging

import psycopg

from tributary_authz import FgaClient, Tuple

log = logging.getLogger("authz_sync")


class Reconciler:
    def __init__(self, fga: FgaClient, conn: psycopg.Connection) -> None:
        self._fga = fga
        self._conn = conn
        self._tuples = fga.read_all()  # single writer: track what's applied after this

    def apply(self, tuples: set[Tuple], access: set[tuple[str, str, str]]) -> dict[str, int]:
        add_t, del_t = tuples - self._tuples, self._tuples - tuples
        if add_t or del_t:
            try:
                self._fga.write(writes=add_t, deletes=del_t)
            finally:
                self._tuples = self._fga.read_all()  # resync if a chunk failed midway
        add_a, del_a = self._apply_access(access)
        return {"tuples_added": len(add_t), "tuples_deleted": len(del_t),
                "access_added": add_a, "access_deleted": del_a}

    def _apply_access(self, access: set[tuple[str, str, str]]) -> tuple[int, int]:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT user_id, company_id, tier FROM authz.user_access FOR UPDATE")
            current = set(cur.fetchall())
            add, delete = access - current, current - access
            if delete:
                cur.executemany("DELETE FROM authz.user_access"
                                " WHERE user_id = %s AND company_id = %s AND tier = %s",
                                sorted(delete))
            if add:
                cur.executemany("INSERT INTO authz.user_access (user_id, company_id, tier)"
                                " VALUES (%s, %s, %s)", sorted(add))
        return len(add), len(delete)
