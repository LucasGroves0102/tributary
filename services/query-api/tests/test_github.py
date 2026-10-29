"""Sign in with GitHub (query_api.github): the state/cookie checks, what a first and a returning
sign-in write, where the token goes, and the assistant caps. GitHub itself is faked: FakeGitHub
for the routes, httpx.MockTransport with GitHub-shaped responses for HttpGitHub. Never calls
GitHub. test_github_db.py runs AppAccounts against the real app schema."""

import json
import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.auth import mint
from query_api.github import (
    COOKIE_PATH,
    NONCE_COOKIE,
    TOKEN_TTL,
    GitHubConfig,
    GitHubError,
    GitHubUser,
    HttpGitHub,
    sign_state,
    state_key,
)
from query_api.sandbox import (
    DEMO_TICKERS,
    DEMO_WATCHLIST,
    DEMO_WORKSPACE,
    QuotaExceeded,
    SandboxConfig,
)

from .conftest import SECRET
from .test_sandbox import IDS, NOW, Clock, FakeSandboxes, FakeStore

CONFIG = GitHubConfig("Iv1.testclientid", "test-client-secret",
                      "https://tributary.example/api/query/v1/auth/github/callback")
QUOTA = SandboxConfig(enabled=True, agent_questions_per_user_per_day=2, agent_questions_per_day=5)
OCTOCAT = GitHubUser(583231, "octocat")
ACCESS_TOKEN = "gho_test-access-token-never-kept"  # a fake (not GitHub's token shape)


class FakeGitHub:
    def __init__(self, user: GitHubUser = OCTOCAT):
        self.user_ = user
        self.codes: list[str] = []

    def exchange(self, code):
        self.codes.append(code)
        if code != "good-code":
            raise GitHubError("no access token")
        return ACCESS_TOKEN

    def user(self, access_token):
        assert access_token == ACCESS_TOKEN
        return self.user_


class FakeAccounts:
    def __init__(self):
        self.accounts = {}  # user_id → NewAccount (first sign-in)
        self.logins = {}
        self.sign_ins = 0
        self.fail = False

    def sign_in(self, account):
        if self.fail:
            raise RuntimeError("app database down")
        self.sign_ins += 1
        user_id = f"gh-{account.user.id}"
        self.logins[user_id] = account.user.login
        if user_id in self.accounts:
            return False
        self.accounts[user_id] = account
        return True

    def login(self, user_id):
        return self.logins.get(user_id)


class QuotaSandboxes(FakeSandboxes):
    """FakeSandboxes that, like AppSandboxes, also caps GitHub accounts."""

    def __init__(self, accounts: FakeAccounts):
        super().__init__()
        self.github = accounts

    def take_question(self, user_id, now, per_user, per_day, questions=1):
        if user_id not in self.github.accounts:
            return super().take_question(user_id, now, per_user, per_day, questions)
        day = now.date()
        mine, total = self.usage.get((user_id, day), 0), self.daily.get(day, 0)
        if mine + questions > per_user:
            raise QuotaExceeded("user", per_user - mine)
        if total + questions > per_day:
            raise QuotaExceeded("day", per_day - total)
        self.usage[(user_id, day)] = mine + questions
        self.daily[day] = total + questions
        return min(per_user - mine, per_day - total) - questions


class Authorizer:
    """OpenFGA as authz-sync leaves it after the account's grants arrive."""

    def __init__(self, accounts: FakeAccounts):
        self.accounts = accounts

    def readable(self, user_id):
        a = self.accounts.accounts.get(user_id)
        return {c: {"financials", "filings"} for c in a.company_ids} if a else {}

    def can(self, user_id, tier, company_id):
        return tier in self.readable(user_id).get(company_id, set())


@pytest.fixture
def world():
    accounts, gh, clock = FakeAccounts(), FakeGitHub(), Clock()
    sandboxes = QuotaSandboxes(accounts)
    app = create_app(Authorizer(accounts), FakeStore(), SECRET, sandboxes=sandboxes,
                     sandbox_config=QUOTA, clock=clock, github_config=CONFIG, github=gh,
                     github_accounts=accounts, demo_users=frozenset({"alice", "gh-583231"}))
    return TestClient(app, follow_redirects=False), accounts, gh, clock


def start(client):
    """GET /start; returns (state, nonce cookie)."""
    r = client.get("/v1/auth/github/start")
    assert r.status_code == 302
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return state, r.cookies[NONCE_COOKIE]


def callback(client, state, nonce, code="good-code", **params):
    headers = {"Cookie": f"{NONCE_COOKIE}={nonce}"} if nonce else {}
    query = {"code": code, "state": state, **params}
    return client.get("/v1/auth/github/callback",
                      params={k: v for k, v in query.items() if v is not None}, headers=headers)


def landing(r) -> dict[str, str]:
    """The fragment's parameters of a redirect to /#/signed-in?..."""
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert (url.scheme, url.netloc, url.path, url.query) == ("", "", "/", "")
    assert url.fragment.startswith("/signed-in?")
    return {k: v[0] for k, v in parse_qs(url.fragment.split("?", 1)[1]).items()}


def sign_in(client):
    return callback(client, *start(client))


def test_routes_404_without_config():
    app = create_app(Authorizer(FakeAccounts()), FakeStore(), SECRET)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/v1/auth/providers").json() == {"github": False}
    assert c.get("/v1/auth/github/start").status_code == 404
    assert c.get("/v1/auth/github/callback", params={"code": "x", "state": "y"}).status_code == 404


def test_config_needs_both_keys():
    assert GitHubConfig.from_env({}) is None
    assert GitHubConfig.from_env({"GITHUB_CLIENT_ID": "id"}) is None
    assert GitHubConfig.from_env({"GITHUB_CLIENT_SECRET": "s"}) is None
    assert GitHubConfig.from_env({"GITHUB_CLIENT_ID": "id", "GITHUB_CLIENT_SECRET": "s"}) == (
        GitHubConfig("id", "s", None))


def test_start_redirects_to_github_with_no_scopes_and_a_browser_bound_state(world):
    client, *_ = world
    assert client.get("/v1/auth/providers").json() == {"github": True}
    r = client.get("/v1/auth/github/start")
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/login/oauth/authorize")
    q = parse_qs(url.query, keep_blank_values=True)
    assert q["client_id"] == [CONFIG.client_id]
    assert q["redirect_uri"] == [CONFIG.redirect_uri]
    assert q["scope"] == [""]  # public profile only
    assert "client_secret" not in q and CONFIG.client_secret not in r.headers["location"]
    nonce = r.cookies[NONCE_COOKIE]
    assert q["state"][0].split(".")[0] == nonce
    cookie = r.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=lax", f"path={COOKIE_PATH}", "max-age=600"):
        assert attr in cookie
    # a fresh nonce each time
    assert start(client)[1] != nonce


def test_first_sign_in_creates_the_demo_account_and_lands_with_a_token_in_the_fragment(world):
    client, accounts, gh, _ = world
    r = sign_in(client)
    got = landing(r)
    assert set(got) == {"token", "expires_in"}
    assert "token" not in urlsplit(r.headers["location"]).query
    claims = jwt.decode(got["token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == "gh-583231"
    assert claims["exp"] - claims["iat"] == TOKEN_TTL == 7 * 24 * 3600
    assert got["expires_in"] == str(TOKEN_TTL)
    # the nonce cookie is cleared: a state can't be replayed
    assert f"{NONCE_COOKIE}=" in r.headers["set-cookie"] and "max-age=0" in (
        r.headers["set-cookie"].lower())
    assert r.headers["cache-control"] == "no-store"
    a = accounts.accounts["gh-583231"]
    assert a.user == OCTOCAT
    assert list(a.company_ids) == [IDS[t] for t in DEMO_TICKERS]
    assert list(a.watchlist) == [IDS[t] for t in DEMO_WATCHLIST]
    assert [w["name"] for w in a.workspaces] == [DEMO_WORKSPACE]
    assert gh.codes == ["good-code"]


def test_the_access_token_is_never_kept_returned_or_logged(world, caplog):
    client, accounts, *_ = world
    with caplog.at_level(logging.DEBUG):
        r = sign_in(client)
    assert ACCESS_TOKEN not in repr(accounts.accounts) + repr(accounts.logins)
    assert ACCESS_TOKEN not in str(r.headers) + r.text
    # the app's own logs (the test client's httpx logger prints the callback URL it sent)
    ours = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records
                    if r.name.startswith(("query_api", "tributary")))
    assert "github sign-in" in ours
    assert ACCESS_TOKEN not in caplog.text
    assert "good-code" not in ours and "token=" not in ours


def test_returning_sign_in_changes_nothing(world):
    client, accounts, _, _ = world
    first = landing(sign_in(client))
    before = accounts.accounts["gh-583231"]
    second = landing(sign_in(client))
    assert accounts.sign_ins == 2
    assert accounts.accounts["gh-583231"] is before  # nothing re-granted or re-seeded
    assert jwt.decode(second["token"], SECRET, algorithms=["HS256"])["sub"] == (
        jwt.decode(first["token"], SECRET, algorithms=["HS256"])["sub"])


@pytest.mark.parametrize("tamper", [
    lambda s: s[:-2] + ("AA" if not s.endswith("AA") else "BB"),  # signature
    lambda s: s.replace(s.split(".")[1], str(int(s.split(".")[1]) + 3600)),  # longer expiry
    lambda s: "x" + s,  # another nonce
    lambda s: s.rsplit(".", 1)[0],  # unsigned
    lambda s: "",
    lambda s: s + "A" * 600,  # oversized: still a redirect, not a 422
])
def test_tampered_state_is_refused(world, tamper):
    client, accounts, gh, _ = world
    state, nonce = start(client)
    assert landing(callback(client, tamper(state), nonce)) == {"error": "state"}
    assert gh.codes == [] and accounts.accounts == {}


def test_expired_state_is_refused(world):
    client, accounts, gh, clock = world
    state, nonce = start(client)
    clock.now = NOW + timedelta(minutes=11)
    assert landing(callback(client, state, nonce)) == {"error": "state"}
    assert gh.codes == [] and accounts.accounts == {}


def test_state_needs_the_nonce_cookie_of_the_browser_that_started(world):
    client, accounts, gh, _ = world
    state, nonce = start(client)
    _, other = start(client)
    assert landing(callback(client, state, None)) == {"error": "state"}
    assert landing(callback(client, state, other)) == {"error": "state"}
    assert gh.codes == [] and accounts.accounts == {}
    assert "token" in landing(callback(client, state, nonce))


def test_state_signed_with_another_secret_is_refused(world):
    client, *_ = world
    forged = sign_state(state_key("another-secret-" + "y" * 32), "n0nce", int(NOW.timestamp())
                        + 600)
    assert landing(callback(client, forged, "n0nce")) == {"error": "state"}


def test_wrong_code_or_denied_consent_or_github_error(world):
    client, accounts, *_ = world
    assert landing(callback(client, *start(client), code="wrong")) == {"error": "github"}
    assert landing(callback(client, *start(client), code=None)) == {"error": "github"}
    assert landing(callback(client, *start(client), code="c" * 600)) == {"error": "github"}
    assert landing(callback(client, *start(client), code=None,
                            error="access_denied")) == {"error": "denied"}
    assert landing(callback(client, *start(client), code=None,
                            error="redirect_uri_mismatch")) == {"error": "github"}
    assert accounts.accounts == {}


def test_app_database_down_or_no_demo_company_is_unavailable(world):
    client, accounts, *_ = world
    accounts.fail = True
    assert landing(sign_in(client)) == {"error": "unavailable"}
    accounts.fail = False
    app = create_app(Authorizer(accounts), FakeStore({}), SECRET, github_config=CONFIG,
                     github=FakeGitHub(), github_accounts=accounts, clock=Clock())
    c = TestClient(app, follow_redirects=False)
    assert landing(sign_in(c)) == {"error": "unavailable"}
    assert accounts.accounts == {}


def test_me_shows_the_github_login_only_for_github_users(world):
    client, *_ = world
    tok = landing(sign_in(client))["token"]
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["user_id"] == "gh-583231"
    assert len(me["companies"]) == len(DEMO_TICKERS)
    assert me["github"]["login"] == "octocat"
    assert sorted(me["github"]["company_ids"]) == sorted(IDS.values())
    assert me["github"]["agent_questions_per_day"] == 2
    assert "sandbox" not in me
    alice = client.get("/v1/me", headers={"Authorization": f"Bearer {mint('alice', SECRET)}"})
    assert "github" not in alice.json()


def test_demo_login_never_mints_a_github_users_token(world):
    client, *_ = world
    # even if an operator listed it in DEMO_USERS
    assert client.post("/v1/demo/token", json={"user_id": "gh-583231"}).status_code == 404
    assert client.post("/v1/demo/token", json={"user_id": "alice"}).status_code == 200


def test_the_assistant_caps_apply_to_github_users(world):
    client, *_ = world
    tok = landing(sign_in(client))["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/v1/demo/agent-quota", headers=h).json() == {
        "demo": True, "remaining_today": 1}
    assert client.post("/v1/demo/agent-quota", headers=h).status_code == 200
    r = client.post("/v1/demo/agent-quota", headers=h)
    assert r.status_code == 429 and r.json()["detail"].startswith("Your account has used its 2")
    # a gh- token without an account (deleted) isn't capped as someone else
    gone = {"Authorization": f"Bearer {mint('gh-999', SECRET)}"}
    assert client.post("/v1/demo/agent-quota", headers=gone).status_code == 401


def test_github_users_are_capped_even_with_sandboxes_off():
    accounts = FakeAccounts()
    app = create_app(Authorizer(accounts), FakeStore(), SECRET,
                     sandboxes=QuotaSandboxes(accounts),
                     sandbox_config=SandboxConfig(enabled=False,
                                                  agent_questions_per_user_per_day=1),
                     clock=Clock(), github_config=CONFIG, github=FakeGitHub(),
                     github_accounts=accounts)
    c = TestClient(app, follow_redirects=False)
    assert c.get("/v1/demo/sandbox").status_code == 404
    h = {"Authorization": f"Bearer {landing(sign_in(c))['token']}"}
    assert c.post("/v1/demo/agent-quota", headers=h).status_code == 200
    assert c.post("/v1/demo/agent-quota", headers=h).status_code == 429


def test_github_accounts_are_not_streamed():
    """Identity, not access: Debezium doesn't capture migration 007's table."""
    connector = json.loads((Path(__file__).parents[3] / "streaming/debezium/app-connector.json")
                           .read_text())
    assert "github" not in connector["table.include.list"]


# HttpGitHub over GitHub-shaped responses (docs.github.com, OAuth web flow and GET /user)

USER_BODY = {"login": "octocat", "id": 583231, "node_id": "MDQ6VXNlcjU4MzIzMQ==",
             "avatar_url": "https://avatars.githubusercontent.com/u/583231?v=4",
             "type": "User", "site_admin": False, "name": "The Octocat", "company": "@github",
             "public_repos": 8, "followers": 9999, "created_at": "2011-01-25T18:44:36Z"}


def mock_github(handler):
    return HttpGitHub(CONFIG, httpx.Client(transport=httpx.MockTransport(handler)))


def test_http_github_exchanges_the_code_and_reads_the_user():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "bearer",
                                             "scope": ""})
        assert str(request.url) == "https://api.github.com/user"
        return httpx.Response(200, json=USER_BODY)

    gh = mock_github(handler)
    assert gh.user(gh.exchange("the-code")) == OCTOCAT
    post, get = seen
    assert post.method == "POST" and post.headers["accept"] == "application/json"
    form = parse_qs(post.content.decode())
    assert form == {"client_id": [CONFIG.client_id], "client_secret": [CONFIG.client_secret],
                    "code": ["the-code"], "redirect_uri": [CONFIG.redirect_uri]}
    assert get.method == "GET" and get.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"error": "bad_verification_code",
                              "error_description": "The code passed is incorrect or expired.",
                              "error_uri": "https://docs.github.com/apps/troubleshooting"}),
    httpx.Response(502, text="bad gateway"),
    httpx.Response(200, text="<html>not json</html>"),
])
def test_http_github_bad_exchange(response):
    gh = mock_github(lambda request: response)
    with pytest.raises(GitHubError):
        gh.exchange("wrong")


@pytest.mark.parametrize("body", [
    {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest"},
    {**USER_BODY, "id": "583231"},
    {**USER_BODY, "id": True},
    {**USER_BODY, "login": "octo cat<script>"},
    {**USER_BODY, "login": "x" * 40},
])
def test_http_github_rejects_unexpected_users(body):
    gh = mock_github(lambda request: httpx.Response(200, json=body))
    with pytest.raises(GitHubError):
        gh.user(ACCESS_TOKEN)


def test_http_github_network_error():
    def down(request):
        raise httpx.ConnectError("unreachable")

    with pytest.raises(GitHubError):
        mock_github(down).exchange("c")
    with pytest.raises(GitHubError):
        mock_github(down).user(ACCESS_TOKEN)

