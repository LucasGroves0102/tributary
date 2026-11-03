"""Connect your data (app migration 011) against the real app database: ownership, the forced
row-level security of data_connections / market_prices / market_price_fetches, the table
checks, that nothing stored is the plain key, and that disconnecting or deleting the user
takes the prices with it. Skipped unless APP_TEST_DATABASE_URL is set; every test is rolled
back."""

import os
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from query_api.connections import AppConnections, UnknownUser
from query_api.keybox import Keyring, SealedKeyError, new_master_key
from query_api.providers import Bar

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

NVDA = "0001045810"
KEY = "test" * 10  # obviously fake: low entropy
NOW = datetime(2026, 11, 2, 15, tzinfo=UTC)
TABLES = ("data_connections", "market_prices", "market_price_fetches")


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


@pytest.fixture
def db():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        conn.execute("INSERT INTO users (user_id, email) VALUES ('t-c1', 't-c1@example.com'),"
                     " ('t-c2', 't-c2@example.com')")
        yield AppConnections(OneConnection(conn)), conn, Keyring.parse(new_master_key())


def stored(c, ring, user):
    c.put(user, "tiingo", ring.seal(user, "tiingo", KEY), "test", NOW)
    c.save_prices(user, "tiingo", NVDA, "NVDA", "ok", [
        Bar(date(2026, 10, 29), Decimal("207.04"), Decimal("207.04"), 300100000),
        Bar(date(2026, 10, 30), Decimal("202.89"), None, None)], NOW)


def test_round_trip_and_owner_only(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    assert [r["provider"] for r in c.mine("t-c1")] == ["tiingo"]
    assert ring.open_("t-c1", "tiingo", c.sealed("t-c1", "tiingo")).reveal() == KEY
    assert [r["day"] for r in c.prices("t-c1", "tiingo", NVDA, None, None)] == \
        [date(2026, 10, 29), date(2026, 10, 30)]
    assert c.latest_day("t-c1", "tiingo", NVDA) == date(2026, 10, 30)
    assert c.fetch_state("t-c1", "tiingo", NVDA)["ticker"] == "NVDA"
    # another user, through the same methods: nothing
    assert c.mine("t-c2") == [] and c.sealed("t-c2", "tiingo") is None
    assert c.prices("t-c2", "tiingo", NVDA, None, None) == []
    assert c.fetch_state("t-c2", "tiingo", NVDA) is None
    assert c.delete("t-c2", "tiingo") is False
    with pytest.raises(UnknownUser):
        c.put("t-nobody", "tiingo", ring.seal("t-nobody", "tiingo", KEY), "test", NOW)


def test_nothing_stored_is_the_plain_key(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    conn.execute("SELECT set_config('app.user_id', 't-c1', true)")
    row = conn.execute("SELECT * FROM data_connections WHERE user_id = 't-c1'").fetchone()
    flat = b"".join(v if isinstance(v, bytes) else str(v).encode() for v in row)
    assert KEY.encode() not in flat and KEY[:20].encode() not in flat


def test_disconnect_deletes_the_key_and_the_prices(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    stored(c, ring, "t-c2")
    assert c.delete("t-c1", "tiingo") is True
    assert c.prices("t-c1", "tiingo", NVDA, None, None) == []
    assert c.fetch_state("t-c1", "tiingo", NVDA) is None
    assert len(c.prices("t-c2", "tiingo", NVDA, None, None)) == 2  # bob's are untouched
    # saving after a disconnect (a fetch that raced it) keeps nothing
    c.save_prices("t-c1", "tiingo", NVDA, "NVDA", "ok",
                  [Bar(date(2026, 10, 31), Decimal("1"), None, None)], NOW)
    assert c.prices("t-c1", "tiingo", NVDA, None, None) == []


def test_deleting_the_user_deletes_everything(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    conn.execute("DELETE FROM users WHERE user_id = 't-c1'")
    conn.execute("SELECT set_config('app.user_id', 't-c1', true)")
    for t in TABLES:
        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))
                            ).fetchone()[0] == 0


def test_row_level_security_binds_the_owner_in_every_table(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'data_connections'").fetchone()[0]
    if conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                    (owner,)).fetchone()[0]:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
    for user in ("t-c2", ""):  # another user, and no user at all
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        for t in TABLES:
            q = sql.Identifier(t)
            assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(q)).fetchone()[0] == 0
            assert conn.execute(sql.SQL("UPDATE {} SET user_id = user_id").format(q)
                                ).rowcount == 0
            assert conn.execute(sql.SQL("DELETE FROM {}").format(q)).rowcount == 0
    # writing a row for someone else is refused (WITH CHECK)
    conn.execute("SELECT set_config('app.user_id', 't-c2', true)")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("INSERT INTO market_prices (user_id, provider, company_id, day, close)"
                     " VALUES ('t-c1', 'tiingo', %s, '2026-10-31', 1)", (NVDA,))
    conn.execute("SELECT set_config('app.user_id', 't-c1', true)")
    assert conn.execute("SELECT count(*) FROM market_prices").fetchone()[0] == 2


def test_a_sealed_key_copied_to_another_users_row_does_not_open(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    stored(c, ring, "t-c2")
    alice = c.sealed("t-c1", "tiingo")
    c.put("t-c2", "tiingo", alice, "test", NOW)  # what a database-level attacker could do
    with pytest.raises(SealedKeyError):
        ring.open_("t-c2", "tiingo", c.sealed("t-c2", "tiingo"))


@pytest.mark.parametrize("column, value", [
    ("provider", "yahoo"), ("key_id", "bad id"), ("wrapped_key", b"\x00" * 59),
    ("nonce", b"\x00" * 11), ("ciphertext", b"\x00" * 300), ("last4", "ab;-"),
])
def test_connection_checks(db, column, value):
    c, conn, ring = db
    s = ring.seal("t-c1", "tiingo", KEY)
    row = {"user_id": "t-c1", "provider": "tiingo", "key_id": s.key_id,
           "wrapped_key": s.wrapped_key, "nonce": s.nonce, "ciphertext": s.ciphertext,
           "last4": "4567", "validated_at": NOW, column: value}
    conn.execute("SELECT set_config('app.user_id', 't-c1', true)")
    cols = sql.SQL(", ").join(map(sql.Identifier, row))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(sql.SQL("INSERT INTO data_connections ({}) VALUES ({})").format(
            cols, sql.SQL(", ").join(sql.Placeholder() * len(row))), list(row.values()))


def test_price_checks(db):
    c, conn, ring = db
    stored(c, ring, "t-c1")
    conn.execute("SELECT set_config('app.user_id', 't-c1', true)")
    for values in [("NVDA", "2026-10-31", 1), (NVDA, "2026-10-31", 0),
                   (NVDA, "2026-10-31", -5)]:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("INSERT INTO market_prices (user_id, provider, company_id, day, close)"
                         " VALUES ('t-c1', 'tiingo', %s, %s, %s)", values)
    # prices need a connection (FK): none for t-c2
    conn.execute("SELECT set_config('app.user_id', 't-c2', true)")
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("INSERT INTO market_prices (user_id, provider, company_id, day, close)"
                     " VALUES ('t-c2', 'tiingo', %s, '2026-10-31', 1)", (NVDA,))
