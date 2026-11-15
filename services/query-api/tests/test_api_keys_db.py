"""API keys against the real app database (migration 019): the forced row-level security (a key
is found by its hash and nothing else is visible before the user is known), what the store takes
and refuses (revoked, expired, a disabled password account, a key older than its password), the
limits counted once across two Query API instances at the same time, the 5-key cap under
concurrency, and keys end to end with the real saved-work store (one user's key never reaches
another's work) and, with TEST_DATABASE_URL as well, the real data store under RLS (a key reads
only what its user's grants allow, even past an authorizer that allows everything).

Statements run as the tables' owner (the Query API's login), whom the forced RLS binds. Skipped
unless APP_TEST_DATABASE_URL is set; the rolled-back tests leave nothing, the concurrent ones
commit and delete their users afterwards."""

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from query_api.api_keys import (
    KEEP_INACTIVE_DAYS,
    MAX_ACTIVE,
    AppApiKeys,
    Limits,
    TooManyKeys,
    UnknownUser,
    key_hash,
    new_key,
)
from query_api.app import create_app
from query_api.store import Store
from query_api.workspaces import AppWorkspaces

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore
from .test_rls import A as CO_A
from .test_rls import AllowAll
from .test_rls import B as CO_B
from .test_workspaces import CHART

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
DATA_DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

A, B = "t-key-a", "t-key-b"
PW = "pw-t-key-c"
NOW = datetime(2026, 11, 15, 12, 0, 30, tzinfo=UTC)
LIMITS = Limits(per_minute=3, per_day=5)
NVDA = "0001045810"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


def owner_of(conn) -> tuple[str, bool]:
    """The app tables' owner, and whether row-level security binds it."""
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'api_keys'").fetchone()[0]
    exempt = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                          (owner,)).fetchone()[0]
    return owner, not exempt


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        owner, _ = owner_of(c)
        c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
        c.execute("INSERT INTO users (user_id, email) VALUES (%s, 'a@example.com'),"
                  " (%s, 'b@example.com'), (%s, 'c@password.invalid')", (A, B, PW))
        c.execute("INSERT INTO password_accounts (user_id, username, password_hash,"
                  " questions_per_day, sessions_valid_after, created_at)"
                  " VALUES (%s, 't-key-c', 'scrypt$n=16384,r=8,p=1$c2FsdA==$aGFzaA==', 5,"
                  " %s, %s)", (PW, NOW - timedelta(days=1), NOW - timedelta(days=1)))
        yield c


@pytest.fixture
def keys(conn):
    return AppApiKeys(OneConnection(conn))


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# --- the tables -------------------------------------------------------------------------------

def test_row_level_security_is_forced_and_a_hash_finds_only_its_own_key(conn, keys):
    owner, binds = owner_of(conn)
    if not binds:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    a, key_a = keys.create(A, "a", ("read",), 90, NOW)
    keys.create(B, "b", ("read",), 90, NOW)
    assert keys.use(key_hash(key_a), NOW, LIMITS) is not None
    for table in ("api_keys", "api_key_usage"):
        forced = conn.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
                              " WHERE oid = %s::regclass", (table,)).fetchone()[0]
        assert forced, table
    conn.execute("SELECT set_config('app.user_id', '', true),"
                 " set_config('app.api_key_hash', '', true)")
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM api_key_usage").fetchone()[0] == 0
    # a key's hash shows that key's row, and nothing else, and can't change it
    conn.execute("SELECT set_config('app.api_key_hash', %s, true)", (key_hash(key_a).hex(),))
    assert conn.execute("SELECT key_id FROM api_keys").fetchall() == [(a["key_id"],)]
    assert conn.execute("UPDATE api_keys SET revoked_at = now()").rowcount == 0
    assert conn.execute("DELETE FROM api_keys").rowcount == 0
    assert conn.execute("SELECT count(*) FROM api_key_usage").fetchone()[0] == 0
    conn.execute("SELECT set_config('app.api_key_hash', %s, true)", ("00" * 32,))
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 0
    # as B: B's key and usage only, and no writing rows for A
    conn.execute("SELECT set_config('app.api_key_hash', '', true),"
                 " set_config('app.user_id', %s, true)", (B,))
    assert [r[0] for r in conn.execute("SELECT user_id FROM api_keys")] == [B]
    assert conn.execute("SELECT count(*) FROM api_key_usage").fetchone()[0] == 0
    assert conn.execute("UPDATE api_keys SET name = 'x' WHERE user_id = %s", (A,)).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("INSERT INTO api_key_usage (key_id, user_id, day, minute)"
                         " VALUES (%s, %s, '2026-11-15', now())", (a["key_id"], A))


def test_the_tables_go_with_the_user(conn, keys):
    _, key = keys.create(A, "a", ("read",), 90, NOW)
    keys.use(key_hash(key), NOW, LIMITS)
    conn.execute("DELETE FROM users WHERE user_id = %s", (A,))
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    assert conn.execute("SELECT count(*) FROM api_keys").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM api_key_usage").fetchone()[0] == 0


# --- what a key is --------------------------------------------------------------------------

def test_only_the_hash_and_prefix_are_stored(conn, keys):
    row, key = keys.create(A, "notebook", ("read", "write"), 30, NOW)
    assert row["prefix"] == key[:8] and row["scopes"] == ["read", "write"]
    assert row["expires_at"] == NOW + timedelta(days=30)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    stored = conn.execute("SELECT row_to_json(k)::text FROM api_keys k").fetchone()[0]
    assert key not in stored and key[8:] not in stored and key[8:20] not in stored
    h = conn.execute("SELECT key_hash FROM api_keys").fetchone()[0]
    assert bytes(h) == key_hash(key)


def test_use_takes_only_a_live_key(conn, keys):
    _, good = keys.create(A, "good", ("read",), 30, NOW)
    revoked, old = keys.create(A, "old", ("read",), 30, NOW)
    assert keys.revoke(A, str(revoked["key_id"]), NOW)
    use = keys.use(key_hash(good), NOW, LIMITS)
    assert use is not None and use.user_id == A and use.scopes == ("read",)
    assert use.outcome == "ok" and use.minute_left == 2 and use.day_left == 4
    assert keys.use(key_hash(old), NOW, LIMITS) is None
    assert keys.use(key_hash(new_key()), NOW, LIMITS) is None
    assert keys.use(key_hash(good), NOW + timedelta(days=30), LIMITS) is None  # expired


def test_a_password_accounts_keys_stop_with_the_account(conn, keys):
    _, key = keys.create(PW, "pw", ("read",), 90, NOW)
    assert keys.use(key_hash(key), NOW, LIMITS) is not None
    # a password change after the key was made: the key goes, like the account's tokens
    conn.execute("UPDATE password_accounts SET sessions_valid_after = %s WHERE user_id = %s",
                 (NOW + timedelta(seconds=10), PW))
    assert keys.use(key_hash(key), NOW + timedelta(minutes=1), LIMITS) is None
    _, newer = keys.create(PW, "newer", ("read",), 90, NOW + timedelta(seconds=20))
    assert keys.use(key_hash(newer), NOW + timedelta(minutes=1), LIMITS) is not None
    conn.execute("UPDATE password_accounts SET disabled = true WHERE user_id = %s", (PW,))
    assert keys.use(key_hash(newer), NOW + timedelta(minutes=2), LIMITS) is None
    # a pw- user without an account row takes no key either
    conn.execute("UPDATE password_accounts SET disabled = false WHERE user_id = %s", (PW,))
    assert keys.use(key_hash(newer), NOW + timedelta(minutes=3), LIMITS) is not None
    conn.execute("DELETE FROM password_accounts WHERE user_id = %s", (PW,))
    assert keys.use(key_hash(newer), NOW + timedelta(minutes=4), LIMITS) is None


def test_revoke_is_the_owners_alone(conn, keys):
    row, key = keys.create(A, "a", ("read",), 90, NOW)
    assert keys.revoke(B, str(row["key_id"]), NOW) is False
    assert keys.revoke(A, str(uuid.uuid4()), NOW) is False
    assert keys.use(key_hash(key), NOW, LIMITS) is not None
    assert keys.revoke(A, str(row["key_id"]), NOW) is True
    assert keys.revoke(A, str(row["key_id"]), NOW) is False  # already


def test_at_most_five_active_and_old_ones_are_pruned(conn, keys):
    made = [keys.create(A, f"k{i}", ("read",), 30, NOW)[0] for i in range(MAX_ACTIVE)]
    with pytest.raises(TooManyKeys):
        keys.create(A, "sixth", ("read",), 30, NOW)
    keys.create(B, "b", ("read",), 30, NOW)  # per user
    keys.revoke(A, str(made[0]["key_id"]), NOW)
    keys.create(A, "sixth", ("read",), 30, NOW)
    assert len(keys.list(A, NOW)) == MAX_ACTIVE + 1  # the revoked one is still listed
    # 30 days after expiring or being revoked, a key goes when its owner makes a new one
    later = NOW + timedelta(days=30 + KEEP_INACTIVE_DAYS, seconds=1)
    assert keys.list(A, later) == []
    keys.create(A, "fresh", ("read",), 30, later)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    assert [r[0] for r in conn.execute("SELECT name FROM api_keys")] == ["fresh"]
    with pytest.raises(UnknownUser):
        keys.create("t-key-nobody", "x", ("read",), 30, NOW)


def test_the_list_shows_todays_usage_and_last_use(conn, keys):
    row, key = keys.create(A, "a", ("read",), 90, NOW)
    for s in (0, 5, 70):
        keys.use(key_hash(key), NOW + timedelta(seconds=s), LIMITS)
    [listed] = keys.list(A, NOW + timedelta(seconds=80))
    assert (listed["requests_today"], listed["limited_today"]) == (3, 0)
    # written at most once a minute
    assert listed["last_used_at"] == NOW + timedelta(seconds=70)
    assert keys.list(A, NOW + timedelta(days=1))[0]["requests_today"] == 0
    assert keys.list(B, NOW) == []


# --- limits ---------------------------------------------------------------------------------

def test_the_minute_and_day_limits_and_retry_after(conn, keys):
    _, key = keys.create(A, "a", ("read",), 90, NOW)
    h = key_hash(key)
    assert [keys.use(h, NOW, LIMITS).outcome for _ in range(4)] == ["ok", "ok", "ok", "minute"]
    refused = keys.use(h, NOW + timedelta(seconds=10), LIMITS)
    assert refused.outcome == "minute" and refused.retry_after == 20
    assert keys.use(h, NOW + timedelta(seconds=30), LIMITS).outcome == "ok"  # next minute
    assert keys.use(h, NOW + timedelta(seconds=31), LIMITS).outcome == "ok"
    day = keys.use(h, NOW + timedelta(seconds=40), LIMITS)
    assert day.outcome == "day"
    assert day.retry_after == int((datetime(2026, 11, 16, tzinfo=UTC)
                                   - NOW - timedelta(seconds=40)).total_seconds())
    [listed] = keys.list(A, NOW)
    assert (listed["requests_today"], listed["limited_today"]) == (5, 3)
    tomorrow = datetime(2026, 11, 16, 0, 0, 1, tzinfo=UTC)
    assert keys.use(h, tomorrow, LIMITS).outcome == "ok"


def test_a_slow_clock_never_resets_the_minute(conn, keys):
    _, key = keys.create(A, "a", ("read",), 90, NOW)
    h = key_hash(key)
    for _ in range(3):
        assert keys.use(h, NOW + timedelta(seconds=40), LIMITS).outcome == "ok"
    # a replica whose clock reads the minute before: still this minute's count
    assert keys.use(h, NOW - timedelta(seconds=35), LIMITS).outcome == "minute"


# --- two Query API instances at once ----------------------------------------------------------

@contextmanager
def committed_users(*users):
    """Users whose rows commit (the concurrent tests need several connections); deleted after,
    with everything that cascades from them."""
    with psycopg.connect(DSN, autocommit=True) as c:
        for u in users:
            c.execute("INSERT INTO users (user_id, email) VALUES (%s, %s)"
                      " ON CONFLICT DO NOTHING", (u, f"{u}@example.com"))
        owner, _ = owner_of(c)
        try:
            yield owner
        finally:
            c.execute("DELETE FROM users WHERE user_id = ANY(%s)", (list(users),))


def owner_pool(owner, size=4):
    def as_owner(c):
        c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
        c.commit()
    return ConnectionPool(DSN, min_size=1, max_size=size, configure=as_owner, open=True)


def test_limits_hold_across_two_instances_counting_at_once():
    user = f"t-key-{uuid.uuid4().hex[:8]}"
    limits = Limits(per_minute=20, per_day=1000)
    with committed_users(user) as owner, owner_pool(owner) as p1, owner_pool(owner) as p2:
        one, two = AppApiKeys(p1), AppApiKeys(p2)
        _, key = one.create(user, "load", ("read",), 30, NOW)
        h, outcomes, lock = key_hash(key), [], threading.Lock()
        start = threading.Barrier(8)

        def hammer(store):
            start.wait()
            for _ in range(10):
                use = store.use(h, NOW, limits)
                with lock:
                    outcomes.append(use.outcome)

        threads = [threading.Thread(target=hammer, args=(one if i % 2 else two,))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert outcomes.count("ok") == 20 and outcomes.count("minute") == 60
        [listed] = two.list(user, NOW)
        assert (listed["requests_today"], listed["limited_today"]) == (20, 60)


def test_the_cap_holds_when_two_instances_create_at_once():
    user = f"t-key-{uuid.uuid4().hex[:8]}"
    with committed_users(user) as owner, owner_pool(owner) as p1, owner_pool(owner) as p2:
        stores, made, refused, lock = [AppApiKeys(p1), AppApiKeys(p2)], [], [], threading.Lock()
        start = threading.Barrier(8)

        def create(i):
            start.wait()
            try:
                stores[i % 2].create(user, f"k{i}", ("read",), 30, NOW)
                with lock:
                    made.append(i)
            except TooManyKeys:
                with lock:
                    refused.append(i)

        threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert (len(made), len(refused)) == (MAX_ACTIVE, 8 - MAX_ACTIVE)


def test_two_api_instances_answer_429_with_retry_after_on_the_shared_count(token):
    user = f"t-key-{uuid.uuid4().hex[:8]}"
    limits = Limits(per_minute=4, per_day=100)
    with committed_users(user) as owner, owner_pool(owner, 2) as p1, owner_pool(owner, 2) as p2:
        apps = [TestClient(create_app(FakeAuthorizer({}), FakeStore(), SECRET,
                                      api_keys=AppApiKeys(p), api_key_limits=limits,
                                      clock=lambda: NOW)) for p in (p1, p2)]
        r = apps[0].post("/v1/api-keys", json={"name": "two", "scopes": ["read"]},
                         headers=token(user))
        key = r.json()["key"]
        answers = [apps[i % 2].get("/v1/me", headers=bearer(key)) for i in range(6)]
        assert [a.status_code for a in answers] == [200, 200, 200, 200, 429, 429]
        assert [a.headers.get("x-ratelimit-remaining-minute") for a in answers[:4]] == \
            ["3", "2", "1", "0"]
        assert answers[4].headers["retry-after"] == "30"
        listed = apps[1].get("/v1/api-keys", headers=token(user)).json()["keys"]
        assert listed[0]["today"] == {"requests": 4, "limited": 2}


# --- end to end ---------------------------------------------------------------------------------

def test_a_key_reaches_only_its_owners_saved_work(conn, keys, token):
    ws = AppWorkspaces(OneConnection(conn))
    client = TestClient(create_app(FakeAuthorizer({}), FakeStore(), SECRET, workspaces=ws,
                                   api_keys=keys, clock=lambda: NOW))
    wb = client.post("/v1/workspaces", json={"name": "B's"}, headers=token(B)).json()
    item = client.post(f"/v1/workspaces/{wb['workspace_id']}/items",
                       json={"kind": "chart", "name": "Rev", "state": CHART},
                       headers=token(B)).json()
    key_a = client.post("/v1/api-keys", json={"name": "a", "scopes": ["read", "write"]},
                        headers=token(A)).json()["key"]
    read_b = client.post("/v1/api-keys", json={"name": "b"}, headers=token(B)).json()["key"]
    assert client.get("/v1/workspaces", headers=bearer(key_a)).json()["workspaces"] == []
    for path in (f"/v1/workspaces/{wb['workspace_id']}", f"/v1/workspace-items/{item['item_id']}"):
        assert client.get(path, headers=bearer(key_a)).status_code == 404
        assert client.get(path, headers=bearer(read_b)).status_code == 200
    r = client.put(f"/v1/workspace-items/{item['item_id']}", json={"state": CHART},
                   headers=bearer(key_a))
    assert r.status_code == 404
    mine = client.post("/v1/workspaces", json={"name": "A's"}, headers=bearer(key_a))
    assert mine.status_code == 201
    assert client.post("/v1/workspaces", json={"name": "x"},
                       headers=bearer(read_b)).status_code == 403
    assert [w["name"] for w in client.get("/v1/workspaces", headers=token(A)).json()[
        "workspaces"]] == ["A's"]


@pytest.mark.skipif(not DATA_DSN, reason="TEST_DATABASE_URL not set")
def test_a_key_reads_only_what_rls_allows_its_user(conn, keys):
    with psycopg.connect(DATA_DSN) as data, data.transaction(force_rollback=True):
        for cid in (CO_A, CO_B):
            data.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                         " VALUES (%s, %s, %s)", (cid, f"Test {cid}", cid))
            data.execute(
                "INSERT INTO gold.quarterly_financials (company_id, metric, period_end, value,"
                " unit, value_source, source_tag, accession_no, filed, source_accessions,"
                " ingested_at) VALUES (%s, 'revenue', '2026-06-30', 1, 'USD', 'reported',"
                " 'us-gaap:Revenues', %s, '2026-08-01', ARRAY[%s], now())",
                (cid, f"{cid}-26-000001", f"{cid}-26-000001"))
        data.execute("DELETE FROM authz.public_access")
        data.execute("DELETE FROM authz.user_access WHERE user_id IN (%s, %s)", (A, B))
        data.execute("INSERT INTO authz.user_access (user_id, company_id, tier)"
                     " VALUES (%s, %s, 'financials')", (A, CO_A))
        data.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')"
                     " ON CONFLICT DO NOTHING", (A,))

        class OneData:
            @contextmanager
            def connection(self):
                yield data

        # layer 1 allows everything: the key's user is all RLS has to go on
        client = TestClient(create_app(AllowAll(), Store(OneData()), SECRET, api_keys=keys,
                                       clock=lambda: NOW))
        _, key_a = keys.create(A, "a", ("read",), 30, NOW)
        _, key_b = keys.create(B, "b", ("read",), 30, NOW)

        def rows(key, company):
            r = client.get(f"/v1/companies/{company}/financials", headers=bearer(key))
            assert r.status_code == 200
            return r.json()["rows"]

        assert len(rows(key_a, CO_A)) == 1
        assert rows(key_a, CO_B) == []
        assert rows(key_b, CO_A) == [] and rows(key_b, CO_B) == []
