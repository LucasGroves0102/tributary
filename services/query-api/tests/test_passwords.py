"""Owner-created username/password accounts (query_api.passwords, query_api.password_accounts):
scrypt hashing and its encoding, the sign-in route (one 401 for every refusal, one scrypt per
attempt, lockout, per-address limit, a body that's never echoed), the session check (disable
and set-password end tokens), /v1/me, the per-account assistant cap and the CLI's password
handling. In-memory fakes; test_password_accounts_db.py runs the same against the real app
schema."""

import hashlib
import io
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from query_api import passwords
from query_api.app import create_app
from query_api.auth import mint
from query_api.authorizer import Access
from query_api.password_accounts import (
    IP_ATTEMPTS,
    LOCK_FOR,
    MAX_FAILURES,
    REFUSED,
    TOKEN_TTL,
    Account,
    IpLimiter,
    read_password,
    run_cli,
)
from query_api.passwords import (
    CURRENT,
    Params,
    PasswordRejected,
    check_policy,
    hash_password,
    needs_rehash,
    normalize_username,
    parse,
    verify,
)
from query_api.sandbox import QuotaExceeded, SandboxConfig, SandboxGone

from .conftest import SECRET
from .test_sandbox import FakeStore

# Obviously fake test passwords (12+ characters).
GOOD = "correct-horse-test-only"
WRONG = "wrong-horse-test-only"
NOW = datetime.now(UTC).replace(microsecond=0)  # tokens are checked against the real clock
USER = "pw-tester"
# hashed once (each scrypt is ~32 MiB and tens of ms)
GOOD_HASH = hash_password(GOOD)


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class Monotonic:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeAccounts:
    """Same rules as AppPasswordAccounts, in memory."""

    def __init__(self):
        self.rows: dict[str, Account] = {}
        self.after: dict[str, datetime] = {}
        self.signed_in: dict[str, datetime] = {}
        self.session_reads = 0

    def add(self, username="tester", password_hash=GOOD_HASH, questions_per_day=5,
            disabled=False, at=NOW):
        user_id = "pw-" + username
        self.rows[username] = Account(user_id, username, password_hash, questions_per_day,
                                      disabled, 0, None)
        self.after[user_id] = at

    def by_id(self, user_id):
        return next(a for a in self.rows.values() if a.user_id == user_id)

    def _set(self, user_id, **kw):
        a = self.by_id(user_id)
        self.rows[a.username] = replace(a, **kw)

    def find(self, username):
        return self.rows.get(username)

    def failed(self, user_id, now):
        a = self.by_id(user_id)
        if a.failed_attempts + 1 >= MAX_FAILURES:
            self._set(user_id, failed_attempts=0, locked_until=now + LOCK_FOR)
        else:
            self._set(user_id, failed_attempts=a.failed_attempts + 1)

    def succeeded(self, user_id, now, new_hash):
        a = self.by_id(user_id)
        self._set(user_id, failed_attempts=0, locked_until=None,
                  password_hash=new_hash or a.password_hash)
        self.signed_in[user_id] = now

    def session(self, user_id):
        self.session_reads += 1
        try:
            a = self.by_id(user_id)
        except StopIteration:
            return None
        return a.disabled, self.after[user_id]

    def info(self, user_id):
        try:
            a = self.by_id(user_id)
        except StopIteration:
            return None
        return {"username": a.username, "questions_per_day": a.questions_per_day}


class AccountSandboxes:
    """take_question as AppSandboxes does it for password accounts: the account's own cap."""

    def __init__(self, accounts: FakeAccounts):
        self.accounts = accounts
        self.usage: dict[str, int] = {}
        self.calls: list[tuple] = []

    def take_question(self, user_id, now, per_user, per_day, questions=1):
        self.calls.append((user_id, per_user, per_day, questions))
        a = self.accounts.info(user_id)
        if a is None or self.accounts.by_id(user_id).disabled:
            raise SandboxGone(user_id)
        cap, mine = a["questions_per_day"], self.usage.get(user_id, 0)
        if mine + questions > cap:
            raise QuotaExceeded("user", cap - mine, cap)
        self.usage[user_id] = mine + questions
        return cap - mine - questions


class NoAuthz:
    def access(self, user_id):
        return Access(frozenset(), {})

    def can(self, *args):
        return False


@pytest.fixture
def world():
    accounts, clock, mono = FakeAccounts(), Clock(), Monotonic()
    accounts.add()
    sandboxes = AccountSandboxes(accounts)
    # the demo's caps are tiny on purpose: an account must not be held to them
    config = SandboxConfig(enabled=False, agent_enabled=False,
                           agent_questions_per_user_per_day=1, agent_questions_per_day=1)
    app = create_app(NoAuthz(), FakeStore(), SECRET, clock=clock, monotonic=mono,
                     password_accounts=accounts, sandboxes=sandboxes, sandbox_config=config)
    return TestClient(app), accounts, clock, mono, sandboxes


def sign_in(client, username="tester", password=GOOD, ip="203.0.113.7"):
    return client.post("/v1/auth/password", json={"username": username, "password": password},
                       headers={"X-Client-IP": ip})


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# --- hashing -------------------------------------------------------------------------------

def test_hash_round_trip_and_encoding():
    params, salt, key = parse(GOOD_HASH)
    assert GOOD_HASH.startswith("scrypt$n=32768,r=8,p=1$")
    assert params == CURRENT == Params(2**15, 8, 1)
    assert len(salt) == 16 and len(key) == 32
    assert verify(GOOD, GOOD_HASH)
    assert not verify(WRONG, GOOD_HASH)
    assert not verify(GOOD + " ", GOOD_HASH)
    assert GOOD not in GOOD_HASH


def test_each_hash_has_its_own_salt():
    other = hash_password(GOOD)
    assert other != GOOD_HASH and parse(other)[1] != parse(GOOD_HASH)[1]
    assert verify(GOOD, other)


def test_hash_matches_scrypt_with_the_encoded_parameters():
    salt = bytes(range(16))
    encoded = hash_password(GOOD, Params(2**10, 8, 1), salt=salt)
    _, _, key = parse(encoded)
    assert key == hashlib.scrypt(GOOD.encode(), salt=salt, n=2**10, r=8, p=1, dklen=32)


def test_weaker_hashes_still_verify_and_ask_for_a_rehash():
    old = hash_password(GOOD, Params(2**10, 8, 1))
    assert verify(GOOD, old)
    assert needs_rehash(old) and not needs_rehash(GOOD_HASH)


@pytest.mark.parametrize("encoded", [
    "", "bcrypt$x", "scrypt$n=33554432,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 44,  # n=2**25
    "scrypt$n=1000,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 44,  # not a power of two
    "scrypt$n=32768,r=99,p=1$AAAAAAAAAAAAAAAAAAAAAA==$" + "A" * 44,
    "scrypt$n=32768,r=8,p=1$AAAA$" + "A" * 44,  # salt too short
])
def test_hashes_out_of_bounds_never_verify(encoded):
    with pytest.raises(ValueError):
        parse(encoded)
    assert not verify(GOOD, encoded)


def test_password_length_policy():
    check_policy("x" * 12)
    check_policy("x" * 256)
    for bad in ("x" * 11, "x" * 257, ""):
        with pytest.raises(PasswordRejected) as e:
            check_policy(bad)
        assert bad not in str(e.value) or bad == ""
    assert not verify("x" * 300, GOOD_HASH)


def test_usernames():
    assert normalize_username("  Tester.One ") == "tester.one"
    for bad in ("ab", "a" * 33, "has space", "émile", "x;drop", ""):
        assert normalize_username(bad) is None


# --- sign-in -------------------------------------------------------------------------------

def test_sign_in_returns_a_seven_day_token(world):
    client, accounts, *_ = world
    r = sign_in(client, username="Tester")  # case and spaces don't matter
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"token", "expires_in"} and body["expires_in"] == TOKEN_TTL == 7 * 86400
    claims = jwt.decode(body["token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == USER and claims["exp"] - claims["iat"] == TOKEN_TTL
    assert accounts.signed_in[USER] == NOW
    me = client.get("/v1/me", headers=bearer(body["token"])).json()
    assert me["account"] == {"username": "tester", "plan": "pro", "agent_questions_per_day": 5}
    assert "github" not in me and "sandbox" not in me


def test_every_refusal_looks_the_same(world, monkeypatch):
    client, accounts, *_ = world
    accounts.add("off", disabled=True)
    accounts.add("locked")
    accounts._set("pw-locked", locked_until=NOW + timedelta(minutes=5))
    passwords.dummy_hash()  # made once per process, before counting
    scrypts = []
    real = hashlib.scrypt
    monkeypatch.setattr(passwords.hashlib, "scrypt",
                        lambda *a, **k: scrypts.append(1) or real(*a, **k))
    answers = []
    for username, password in [("nobody", GOOD), ("tester", WRONG), ("off", GOOD),
                               ("locked", GOOD), ("NOT A NAME!", GOOD), ("tester", "short")]:
        before = len(scrypts)
        r = sign_in(client, username, password)
        answers.append((r.status_code, r.text))
        # one scrypt each, so an unknown name takes as long as a wrong password
        assert len(scrypts) - before == 1, username
    assert set(answers) == {(401, '{"detail":"' + REFUSED + '"}')}


def test_lockout_after_ten_failures_and_reset_on_success(world):
    client, accounts, clock, *_ = world
    # 9 failures, then a success: the count starts again
    for i in range(MAX_FAILURES - 1):
        assert sign_in(client, password=WRONG, ip=f"198.51.100.{i}").status_code == 401
    assert accounts.rows["tester"].failed_attempts == MAX_FAILURES - 1
    assert sign_in(client).status_code == 200
    assert accounts.rows["tester"].failed_attempts == 0
    for i in range(MAX_FAILURES):
        assert sign_in(client, password=WRONG, ip=f"198.51.100.{i}").status_code == 401
    assert accounts.rows["tester"].locked_until == NOW + LOCK_FOR
    # locked: even the right password is refused, with the same answer
    r = sign_in(client, ip="192.0.2.1")
    assert (r.status_code, r.json()["detail"]) == (401, REFUSED)
    clock.now = NOW + LOCK_FOR + timedelta(seconds=1)
    assert sign_in(client, ip="192.0.2.1").status_code == 200
    assert accounts.rows["tester"].locked_until is None


def test_attempts_per_address(world):
    client, _, _, mono, _ = world
    for _ in range(IP_ATTEMPTS):
        assert sign_in(client, password=WRONG, username="nobody").status_code == 401
    r = sign_in(client)
    assert r.status_code == 429 and "Too many sign-in attempts" in r.json()["detail"]
    assert sign_in(client, ip="192.0.2.9").status_code == 200  # another address
    mono.t += 15 * 60
    assert sign_in(client).status_code == 200


def test_ip_limiter_forgets_idle_addresses():
    mono = Monotonic()
    lim = IpLimiter(limit=2, window=60, monotonic=mono)
    assert lim.take("a") and lim.take("a") and not lim.take("a")
    mono.t += 61
    assert lim.take("b") and "a" not in lim._events
    assert lim.take("a")


@pytest.mark.parametrize("body", [
    {"username": "tester"},
    {"username": "tester", "password": GOOD, "remember": True},
    {"username": "tester", "password": 123456789012},
    ["tester", GOOD],
    "not json",
])
def test_a_malformed_body_is_never_echoed(world, body):
    client, *_ = world
    kwargs = {"content": body} if isinstance(body, str) else {"json": body}
    r = client.post("/v1/auth/password", **kwargs)
    assert r.status_code == 422
    assert GOOD not in r.text and "123456789012" not in r.text


def test_an_oversized_body_is_refused_unread(world):
    client, *_ = world
    r = client.post("/v1/auth/password", json={"username": "tester", "password": "x" * 5000})
    assert r.status_code == 413 and "xxxx" not in r.text


def test_no_password_or_token_in_logs_or_errors(world, caplog):
    caplog.set_level(logging.DEBUG)
    client, *_ = world
    token = sign_in(client).json()["token"]
    for username, password in [("tester", WRONG), ("nobody-" + GOOD[:8], GOOD)]:
        sign_in(client, username, password)
    logged = caplog.text + " ".join(str(r.__dict__) for r in caplog.records)
    for secret in (GOOD, WRONG, token, "nobody-"):
        assert secret not in logged
    assert "pw-tester" in logged  # known accounts are logged by user id


def test_no_route_without_an_app_database():
    client = TestClient(create_app(NoAuthz(), FakeStore(), SECRET))
    assert client.post("/v1/auth/password", json={"username": "tester", "password": GOOD}
                       ).status_code == 404
    assert client.get("/v1/auth/providers").json()["password"] is False
    # and a "pw-" token can't be checked, so it isn't accepted
    assert client.get("/v1/me", headers=bearer(mint(USER, SECRET))).status_code == 401


def test_providers_offer_password_sign_in(world):
    client, *_ = world
    assert client.get("/v1/auth/providers").json() == {"github": False, "password": True}


def test_demo_token_never_signs_in_a_password_account():
    app = create_app(NoAuthz(), FakeStore(), SECRET, demo_users=frozenset({USER}),
                     password_accounts=FakeAccounts())
    assert TestClient(app).post("/v1/demo/token", json={"user_id": USER}).status_code == 404


# --- sessions ------------------------------------------------------------------------------

def test_disable_ends_sessions_after_the_cache(world):
    client, accounts, _, mono, _ = world
    token = sign_in(client).json()["token"]
    assert client.get("/v1/me", headers=bearer(token)).status_code == 200
    accounts._set(USER, disabled=True)
    mono.t += 11  # SESSION_CACHE_SECONDS
    assert client.get("/v1/me", headers=bearer(token)).status_code == 401
    assert sign_in(client).status_code == 401


def test_a_password_change_ends_older_tokens(world):
    client, accounts, clock, mono, _ = world
    old = mint(USER, SECRET, 3600, now=int(NOW.timestamp()))
    assert client.get("/v1/me", headers=bearer(old)).status_code == 200
    accounts.after[USER] = NOW + timedelta(seconds=5)  # set-password, 5 s later
    clock.now = NOW + timedelta(seconds=5)
    mono.t += 11
    assert client.get("/v1/me", headers=bearer(old)).status_code == 401
    new = sign_in(client).json()["token"]
    assert client.get("/v1/me", headers=bearer(new)).status_code == 200


def test_session_checks_are_cached(world):
    client, accounts, *_ = world
    token = sign_in(client).json()["token"]
    for _ in range(5):
        client.get("/v1/me", headers=bearer(token))
    assert accounts.session_reads == 1


def test_a_token_for_a_deleted_account_is_refused(world):
    client, *_ = world
    assert client.get("/v1/me", headers=bearer(mint("pw-ghost", SECRET))).status_code == 401


# --- assistant quota -----------------------------------------------------------------------

def test_quota_is_the_accounts_own(world):
    client, _, _, _, sandboxes = world
    h = bearer(sign_in(client).json()["token"])
    # the demo's caps (1 per user, 1 per day, assistant off for demos) don't apply
    for left in (4, 3):
        r = client.post("/v1/demo/agent-quota", headers=h)
        assert r.json() == {"demo": True, "remaining_today": left}
    r = client.post("/v1/demo/agent-quota", headers=h, json={"questions": 4})
    assert r.status_code == 429
    assert "counts as 4 assistant questions" in r.json()["detail"]
    assert "your account has 3 left today" in r.json()["detail"]
    assert client.post("/v1/demo/agent-quota", headers=h, json={"questions": 3}).status_code \
        == 200
    r = client.post("/v1/demo/agent-quota", headers=h)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Your account has used its 5 assistant questions")


def test_quota_zero_turns_the_assistant_off(world):
    client, accounts, *_ = world
    accounts.add("quiet", questions_per_day=0)
    h = bearer(sign_in(client, "quiet").json()["token"])
    r = client.post("/v1/demo/agent-quota", headers=h)
    assert r.status_code == 429 and "no assistant questions" in r.json()["detail"]


# --- the CLI's password handling -----------------------------------------------------------

class Tty(io.StringIO):
    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


def test_password_prompted_twice_on_a_terminal():
    answers = iter([GOOD, GOOD])
    assert read_password(Tty(), lambda _: next(answers)) == GOOD
    answers = iter([GOOD, WRONG])
    with pytest.raises(PasswordRejected, match="don't match"):
        read_password(Tty(), lambda _: next(answers))


def test_password_from_stdin_when_not_a_terminal():
    assert read_password(Pipe(GOOD + "\n"), lambda _: pytest.fail("prompted")) == GOOD
    with pytest.raises(PasswordRejected, match="at least 12"):
        read_password(Pipe("short\n"))


class CliStore:
    def __init__(self):
        self.created = []

    def create(self, username, password_hash, questions_per_day, at, companies, watch, ws):
        self.created.append((username, password_hash, questions_per_day, companies))
        return "pw-" + username


def test_cli_never_prints_the_password_or_takes_it_as_an_argument():
    store, out, err = CliStore(), io.StringIO(), io.StringIO()
    code = run_cli(["create", "tester", "--questions-per-day", "100"], store,  # type: ignore
                   access=lambda: (["0000000001"], [], []), out=out, err=err,
                   stdin=Pipe(GOOD + "\n"), clock=lambda: NOW)
    assert code == 0
    (username, password_hash, cap, companies), = store.created
    assert (username, cap, companies) == ("tester", 100, ["0000000001"])
    assert verify(GOOD, password_hash)
    assert GOOD not in out.getvalue() + err.getvalue() and "created tester" in out.getvalue()
    for argv in (["create", "tester", "--questions-per-day", "1", "--password", GOOD],
                 ["create", "tester", "--questions-per-day", "1001"],
                 ["create", "Tester", "--questions-per-day", "1"],
                 ["create", "tester"]):
        with pytest.raises(SystemExit):
            run_cli(argv, store, access=lambda: ([], [], []),  # type: ignore
                    out=out, err=io.StringIO(), stdin=Pipe(GOOD + "\n"))


def test_password_accounts_are_not_streamed():
    """Identity, not access (migration 013): Debezium doesn't capture it."""
    import json
    from pathlib import Path

    connector = json.loads((Path(__file__).parents[3] / "streaming/debezium/app-connector.json")
                           .read_text())
    assert "password_accounts" not in connector["table.include.list"]
