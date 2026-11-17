"""Connected apps against the real app database (migration 025): the forced row-level security
(a code or token is found by its hash and nothing else is visible before the user is known),
codes and refresh tokens spent once even when presented twice at the same moment (the second
revokes the grant), the password-account rule, the demo-sandbox CHECK, the consent spent once,
registrations pruned without touching clients that have grants (RLS hides grants from the
prune), limits counted once across two Query API instances, and Disconnect.

Statements run as the tables' owner (the Query API's login), whom the forced RLS binds. Skipped
unless APP_TEST_DATABASE_URL is set; the rolled-back tests leave nothing, the concurrent ones
commit and delete their users afterwards."""

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from query_api.api_keys import Limits
from query_api.oauth import (
    ACCESS_PREFIX,
    MAX_GRANTS,
    REFRESH_PREFIX,
    AlreadyDecided,
    AppOAuth,
    TokenPair,
    TooManyGrants,
    digest,
    new_token,
)

psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402

DSN = os.environ.get("APP_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="APP_TEST_DATABASE_URL not set")

A, B = "t-oauth-a", "t-oauth-b"
PW = "pw-t-oauth-c"
NOW = datetime(2026, 11, 17, 9, 0, 30, tzinfo=UTC)
RESOURCE = "https://tributary.example/mcp"
CB = "https://claude.ai/api/mcp/auth_callback"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
LIMITS = Limits(per_minute=3, per_day=5)


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


def owner_of(conn) -> tuple[str, bool]:
    owner = conn.execute("SELECT tableowner FROM pg_tables WHERE schemaname = 'public'"
                         " AND tablename = 'oauth_grants'").fetchone()[0]
    exempt = conn.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = %s",
                          (owner,)).fetchone()[0]
    return owner, not exempt


def add_users(c):
    c.execute("INSERT INTO users (user_id, email) VALUES (%s, 'oa@example.com'),"
              " (%s, 'ob@example.com'), (%s, 'oc@password.invalid')", (A, B, PW))
    c.execute("INSERT INTO password_accounts (user_id, username, password_hash,"
              " questions_per_day, sessions_valid_after, created_at)"
              " VALUES (%s, 't-oauth-c', 'scrypt$n=16384,r=8,p=1$c2FsdA==$aGFzaA==', 5,"
              " %s, %s)", (PW, NOW - timedelta(days=1), NOW - timedelta(days=1)))


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        owner, _ = owner_of(c)
        c.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
        add_users(c)
        yield c


@pytest.fixture
def store(conn):
    return AppOAuth(OneConnection(conn))


def pair():
    return TokenPair(new_token(ACCESS_PREFIX), new_token(REFRESH_PREFIX))


def client_of(store, name="Claude", uris=(CB,)):
    cid = "trc_" + uuid.uuid4().hex
    return store.register(cid, name, None, uris, "none", None, NOW)


def approved(store, user=A, client=None, consent=None):
    client = client or client_of(store)
    code = new_token("")
    grant = store.approve(user, client.client_key, RESOURCE, CB, CHALLENGE, digest(code),
                          digest(consent or uuid.uuid4().hex), NOW)
    return client, grant, code


def ok(_):
    return True


def connected(store, user=A):
    client, grant, code = approved(store, user)
    tokens = pair()
    issued = store.exchange_code(digest(code), NOW, ok, tokens)
    assert issued.outcome == "ok" and issued.user_id == user
    return client, grant, tokens


# --- the tables ----------------------------------------------------------------------------------

def test_row_level_security_is_forced_and_a_hash_finds_only_its_own_row(conn, store):
    owner, binds = owner_of(conn)
    if not binds:
        pytest.skip(f"table owner {owner} bypasses row-level security")
    _, _, tokens = connected(store, A)
    connected(store, B)
    for table in ("oauth_grants", "oauth_codes", "oauth_tokens", "oauth_usage"):
        forced = conn.execute("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
                              " WHERE oid = %s::regclass", (table,)).fetchone()[0]
        assert forced, table
    conn.execute("SELECT set_config('app.user_id', '', true),"
                 " set_config('app.oauth_token_hash', '', true),"
                 " set_config('app.oauth_code_hash', '', true)")
    for table in ("oauth_grants", "oauth_codes", "oauth_tokens"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    # a token's hash shows that token's row, nothing else, and can't change it
    h = digest(tokens.access)
    conn.execute("SELECT set_config('app.oauth_token_hash', %s, true)", (h.hex(),))
    assert conn.execute("SELECT count(*) FROM oauth_tokens").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM oauth_grants").fetchone()[0] == 0
    assert conn.execute("UPDATE oauth_tokens SET used_at = now()").rowcount == 0
    assert conn.execute("DELETE FROM oauth_tokens").rowcount == 0
    # one user sees only their own
    conn.execute("SELECT set_config('app.oauth_token_hash', '', true),"
                 " set_config('app.user_id', %s, true)", (B,))
    assert {r[0] for r in conn.execute("SELECT user_id FROM oauth_grants")} == {B}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("INSERT INTO oauth_grants (user_id, client_key, scopes, resource,"
                     " consent_hash, created_at) SELECT %s, client_key, '{read}', %s,"
                     " sha256('x'), now() FROM oauth_clients LIMIT 1", (A, RESOURCE))


def test_only_hashes_are_stored(conn, store):
    _, _, tokens = connected(store)
    conn.execute("SELECT set_config('app.user_id', %s, true)", (A,))
    rows = conn.execute("SELECT token_hash, prefix FROM oauth_tokens").fetchall()
    assert {bytes(r[0]) for r in rows} == {digest(tokens.access), digest(tokens.refresh)}
    assert {r[1] for r in rows} == {tokens.access[:8], tokens.refresh[:8]}


def test_the_whole_life_of_a_connection(conn, store):
    client, grant, tokens = connected(store)
    use = store.use(digest(tokens.access), NOW, LIMITS)
    assert use and use.user_id == A and use.client_name == "Claude" and use.resource == RESOURCE
    # the refresh token rotates once; presented again it revokes everything
    new = pair()
    assert store.refresh(digest(tokens.refresh), NOW + timedelta(minutes=55), ok,
                         new).outcome == "ok"
    assert store.use(digest(new.access), NOW + timedelta(minutes=56), LIMITS) is not None
    again = store.refresh(digest(tokens.refresh), NOW + timedelta(minutes=57), ok, pair())
    assert again.outcome == "reused"
    assert store.use(digest(new.access), NOW + timedelta(minutes=58), LIMITS) is None
    assert store.refresh(digest(new.refresh), NOW + timedelta(minutes=58), ok,
                         pair()).outcome == "invalid"


def test_a_code_is_spent_on_first_presentation_whatever_follows(conn, store):
    _, _, code = approved(store)
    assert store.exchange_code(digest(code), NOW, lambda p: False, pair()).outcome == "invalid"
    assert store.exchange_code(digest(code), NOW, ok, pair()).outcome == "reused"


def test_a_code_after_60_seconds_is_refused(conn, store):
    _, _, code = approved(store)
    late = NOW + timedelta(seconds=61)
    assert store.exchange_code(digest(code), late, ok, pair()).outcome == "invalid"


def test_the_check_sees_the_codes_binding(conn, store):
    client, _, code = approved(store)
    seen = []
    store.exchange_code(digest(code), NOW, lambda p: seen.append(p) or True, pair())
    [p] = seen
    assert (p.client_key, p.redirect_uri, p.code_challenge, p.resource) == (
        client.client_key, CB, CHALLENGE, RESOURCE)


def test_disconnect_revokes_every_grant_of_that_client_for_that_user_only(conn, store):
    client, _, tokens = connected(store, A)
    code_b = "B" * 43
    store.approve(B, client.client_key, RESOURCE, CB, CHALLENGE, digest(code_b),
                  digest("b-consent"), NOW)
    tokens_b = pair()
    assert store.exchange_code(digest(code_b), NOW, ok, tokens_b).outcome == "ok"
    [row] = store.connections(A, NOW)
    assert row["client_name"] == "Claude" and str(row["client_key"]) == client.client_key
    assert store.disconnect(B, client.client_key, NOW) == 1  # bob's own grant only
    assert store.use(digest(tokens.access), NOW, LIMITS) is not None
    assert store.disconnect(A, client.client_key, NOW) == 1
    assert store.use(digest(tokens.access), NOW, LIMITS) is None
    assert store.connections(A, NOW) == []
    assert store.disconnect(A, client.client_key, NOW) == 0


def test_the_revocation_endpoint_needs_the_tokens_own_client(conn, store):
    client, _, tokens = connected(store)
    other = client_of(store, "Other")
    assert store.revoke(digest(tokens.refresh), other.client_key, NOW) is False
    assert store.use(digest(tokens.access), NOW, LIMITS) is not None
    assert store.revoke(digest(tokens.refresh), client.client_key, NOW) is True
    assert store.use(digest(tokens.access), NOW, LIMITS) is None


def test_a_password_accounts_grants_die_with_a_disable_or_password_change(conn, store):
    _, _, tokens = connected(store, PW)
    assert store.use(digest(tokens.access), NOW, LIMITS) is not None
    conn.execute("UPDATE password_accounts SET sessions_valid_after = %s WHERE user_id = %s",
                 (NOW + timedelta(seconds=1), PW))
    assert store.use(digest(tokens.access), NOW + timedelta(seconds=2), LIMITS) is None
    assert store.refresh(digest(tokens.refresh), NOW + timedelta(seconds=2), ok,
                         pair()).outcome == "invalid"


def test_a_demo_sandbox_cant_hold_a_grant(conn, store):
    conn.execute("INSERT INTO users (user_id, email) VALUES ('demo-t-oauth', 'd@x.invalid')")
    client = client_of(store)
    with pytest.raises(psycopg.errors.CheckViolation):
        store.approve("demo-t-oauth", client.client_key, RESOURCE, CB, CHALLENGE,
                      digest("c" * 43), digest("x"), NOW)


def test_a_consent_is_approved_once(conn, store):
    client = client_of(store)
    approved(store, client=client, consent="same")
    with pytest.raises(AlreadyDecided):
        approved(store, client=client, consent="same")


def test_at_most_max_grants_live(conn, store):
    client = client_of(store)
    for _ in range(MAX_GRANTS):
        approved(store, client=client)
    with pytest.raises(TooManyGrants):
        approved(store, client=client)


def test_grants_that_never_got_a_token_dont_count_after_an_hour(conn, store):
    client = client_of(store)
    for _ in range(MAX_GRANTS):
        approved(store, client=client)
    later = NOW + timedelta(hours=2)
    store.approve(A, client.client_key, RESOURCE, CB, CHALLENGE, digest("z" * 43),
                  digest("later"), later)


def test_pruning_registrations_never_touches_a_used_client(conn, store):
    owner, binds = owner_of(conn)
    old = NOW - timedelta(days=2)
    unused = store.register("trc_" + "u" * 32, "Unused", None, (CB,), "none", None, old)
    used = store.register("trc_" + "v" * 32, "Used", None, (CB,), "none", None, old)
    code = "U" * 43
    store.approve(A, used.client_key, RESOURCE, CB, CHALLENGE, digest(code), digest("u"), old)
    tokens = pair()
    assert store.exchange_code(digest(code), old, ok, tokens).outcome == "ok"
    client_of(store)  # a new registration prunes
    assert store.client(unused.client_id) is None
    assert store.client(used.client_id) is not None
    assert store.refresh(digest(tokens.refresh), NOW, ok, pair()).outcome == "ok"


def test_a_metadata_document_is_cached_and_refreshed_in_place(conn, store):
    url = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    a = store.save_document(url, "Claude", None, (CB,), NOW, NOW + timedelta(hours=1))
    b = store.save_document(url, "Claude 2", None, (CB, "https://claude.com/cb"),
                            NOW + timedelta(hours=2), NOW + timedelta(hours=3))
    assert a.client_key == b.client_key and b.name == "Claude 2" and len(b.redirect_uris) == 2
    with pytest.raises(psycopg.errors.CheckViolation):
        store.register("https://evil.example/doc", "Evil", None, (CB,), "none", None, NOW)


@pytest.mark.parametrize("bad", [
    {"redirect_uris": []}, {"auth_method": "client_secret_basic"},
    {"name": "x\x01y"}, {"name": ""}])
def test_the_clients_checks(conn, store, bad):
    args = {"name": "ok", "redirect_uris": (CB,), "auth_method": "none"}
    args.update(bad)
    with pytest.raises(psycopg.errors.CheckViolation):
        store.register("trc_" + "w" * 32, args["name"], None, tuple(args["redirect_uris"]),
                       args["auth_method"], None, NOW)


# --- at the same moment (committed; cleaned up) --------------------------------------------------

@pytest.fixture
def committed():
    pool_conns = []

    class Pool:
        @contextmanager
        def connection(self):
            c = psycopg.connect(DSN)
            pool_conns.append(c)
            try:
                with c:
                    owner, _ = owner_of(c)
                    c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
                    yield c
            finally:
                c.close()

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM users WHERE user_id IN (%s, %s, %s)", (A, B, PW))
        add_users(c)
    yield AppOAuth(Pool())
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DELETE FROM users WHERE user_id IN (%s, %s, %s)", (A, B, PW))
        c.execute("DELETE FROM oauth_clients WHERE client_name LIKE 't-oauth %%'")


def race(fn, n=2):
    barrier, results = threading.Barrier(n), [None] * n

    def run(i):
        barrier.wait()
        results[i] = fn()
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def test_a_code_presented_twice_at_once_is_issued_once_and_then_revoked(committed):
    store = committed
    client = store.register("trc_" + uuid.uuid4().hex, "t-oauth race", None, (CB,), "none",
                            None, NOW)
    code = "R" * 43
    store.approve(A, client.client_key, RESOURCE, CB, CHALLENGE, digest(code),
                  digest(uuid.uuid4().hex), NOW)
    pairs = [pair(), pair()]
    i = iter(range(2))
    out = race(lambda: store.exchange_code(digest(code), NOW, ok, pairs[next(i)]))
    assert sorted(o.outcome for o in out) == ["ok", "reused"]
    # the one that got tokens has them revoked by the second presentation
    assert all(store.use(digest(p.access), NOW, LIMITS) is None for p in pairs)


def test_a_refresh_token_presented_twice_at_once_rotates_once(committed):
    store = committed
    client = store.register("trc_" + uuid.uuid4().hex, "t-oauth race 2", None, (CB,), "none",
                            None, NOW)
    code = "S" * 43
    store.approve(A, client.client_key, RESOURCE, CB, CHALLENGE, digest(code),
                  digest(uuid.uuid4().hex), NOW)
    first = pair()
    assert store.exchange_code(digest(code), NOW, ok, first).outcome == "ok"
    pairs = [pair(), pair()]
    i = iter(range(2))
    out = race(lambda: store.refresh(digest(first.refresh), NOW, ok, pairs[next(i)]))
    assert sorted(o.outcome for o in out) == ["ok", "reused"]
    assert all(store.use(digest(p.access), NOW, LIMITS) is None for p in pairs)


def test_limits_hold_across_two_instances(committed):
    store_a, store_b = committed, AppOAuth(committed._pool)
    client = store_a.register("trc_" + uuid.uuid4().hex, "t-oauth limits", None, (CB,), "none",
                              None, NOW)
    code = "L" * 43
    store_a.approve(A, client.client_key, RESOURCE, CB, CHALLENGE, digest(code),
                    digest(uuid.uuid4().hex), NOW)
    tokens = pair()
    assert store_a.exchange_code(digest(code), NOW, ok, tokens).outcome == "ok"
    stores = [store_a, store_b] * 3
    i = iter(range(6))
    out = race(lambda: stores[next(i)].use(digest(tokens.access), NOW, LIMITS), 6)
    assert sorted(o.outcome for o in out) == ["minute", "minute", "minute", "ok", "ok", "ok"]
