"""The single-file client against a fake Query API on this machine (a real HTTP server on a free
port, so urllib does everything it does in production): the key only in the Authorization header,
parameters and bodies, waiting out a 429, errors with the API's detail, CSV, and that the key never
follows a redirect or shows in an error or repr."""

import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "tributary_client", Path(__file__).resolve().parents[1] / "tributary_client.py")
assert _spec and _spec.loader
tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tc)

KEY = "trb_" + "k" * 43  # a fake: the client only checks the shape


class FakeApi:
    """Records each request; answers from `script` (path -> list of (status, headers, body)),
    the last answer repeating."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: dict[str, list[tuple[int, dict, bytes]]] = {}
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                url = urllib.parse.urlsplit(self.path)
                api.requests.append({
                    "method": self.command, "path": url.path,
                    "query": urllib.parse.parse_qs(url.query),
                    "headers": dict(self.headers.items()),
                    "body": self.rfile.read(length) if length else b""})
                answers = api.script.get(url.path) or [(404, {}, b'{"detail": "Not Found"}')]
                status, headers, body = answers.pop(0) if len(answers) > 1 else answers[0]
                self.send_response(status)
                headers = {"Content-Type": "application/json", **headers}
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/query"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def answer(self, path, *answers):
        self.script["/api/query" + path] = [
            (s, h, b if isinstance(b, bytes) else json.dumps(b).encode()) for s, h, b in answers]


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def slept():
    return []


@pytest.fixture
def client(api, slept):
    return tc.Tributary(KEY, api.url, sleep=slept.append)


def test_the_key_goes_in_the_authorization_header_only(api, client):
    api.answer("/v1/me", (200, {}, {"user_id": "gh-1", "api_key": {"scopes": ["read"]}}))
    assert client.me()["user_id"] == "gh-1"
    [r] = api.requests
    assert r["method"] == "GET" and r["path"] == "/api/query/v1/me" and r["query"] == {}
    assert r["headers"]["Authorization"] == f"Bearer {KEY}"
    others = {k: v for k, v in r["headers"].items() if k != "Authorization"}
    assert KEY not in json.dumps(others) and KEY not in r["path"]
    assert r["headers"]["User-Agent"].startswith("tributary-client/")


def test_parameters_lists_and_bodies(api, client):
    nvda = "0001045810"
    api.answer(f"/v1/companies/{nvda}/financials", (200, {}, {"rows": []}))
    api.answer("/v1/workspaces", (201, {}, {"workspace_id": "w1"}))
    client.financials(nvda, metrics=["revenue", "net_income"], quarters=8, readings=True)
    q = api.requests[0]["query"]
    assert q == {"metrics": ["revenue", "net_income"], "quarters": ["8"], "view": ["quarter"],
                 "basis": ["fiscal"], "readings": ["true"]}
    assert client.create_workspace("From a script") == {"workspace_id": "w1"}
    sent = api.requests[1]
    assert sent["method"] == "POST" and json.loads(sent["body"]) == {"name": "From a script"}
    assert sent["headers"]["Content-Type"] == "application/json"


def test_csv_comes_back_as_text(api, client):
    api.answer("/v1/companies/0001045810/financials",
               (200, {"Content-Type": "text/csv; charset=utf-8"}, b"metric,value\nrevenue,1\n"))
    assert client.financials("0001045810", csv=True) == "metric,value\nrevenue,1\n"
    assert api.requests[0]["query"]["format"] == ["csv"]


def test_a_429_waits_for_retry_after_and_tries_again(api, client, slept):
    api.answer("/v1/me", (429, {"Retry-After": "7"}, {"detail": "limit"}),
               (200, {}, {"user_id": "gh-1"}))
    assert client.me() == {"user_id": "gh-1"}
    assert slept == [7] and len(api.requests) == 2


def test_it_stops_retrying_and_says_when(api, slept):
    client = tc.Tributary(KEY, api.url, retries=1, sleep=slept.append)
    api.answer("/v1/me", (429, {"Retry-After": "3"}, {"detail": "this API key made 60"
                                                                " requests this minute"}))
    with pytest.raises(tc.TributaryError) as e:
        client.me()
    assert e.value.status == 429 and e.value.retry_after == 3 and "60 requests" in e.value.detail
    assert slept == [3] and len(api.requests) == 2
    # a wait longer than max_wait isn't slept through
    patient = tc.Tributary(KEY, api.url, max_wait=2, sleep=slept.append)
    with pytest.raises(tc.TributaryError):
        patient.me()
    assert slept == [3]


@pytest.mark.parametrize("status, detail", [
    (401, "invalid token"), (403, "this API key can't write: it has the read scope"),
    (404, "no such item"), (503, "sign-in check unavailable")])
def test_refusals_carry_the_apis_detail_and_never_the_key(api, client, status, detail):
    api.answer("/v1/workspaces", (status, {}, {"detail": detail}))
    with pytest.raises(tc.TributaryError) as e:
        client.create_workspace("x")
    assert (e.value.status, e.value.detail) == (status, detail)
    assert KEY not in str(e.value) and KEY not in repr(client) and KEY[8:] not in repr(client)
    assert repr(client).endswith("key=trb_kkkk...)")


def test_a_redirect_is_never_followed(api, client):
    api.answer("/v1/me", (302, {"Location": "http://127.0.0.1:9/elsewhere"}, b""))
    with pytest.raises(tc.TributaryError) as e:
        client.me()
    assert e.value.status == 302 and "redirect" in e.value.detail
    assert len(api.requests) == 1


def test_an_api_that_cant_be_reached(client):
    down = tc.Tributary(KEY, "http://127.0.0.1:9/api/query", timeout=2)
    with pytest.raises(tc.TributaryError) as e:
        down.me()
    assert e.value.status is None and KEY not in str(e.value)


@pytest.mark.parametrize("key, base", [
    ("", None), ("trb_short", None), ("sk-" + "x" * 44, None),
    (KEY, "http://tributary.example.com/api/query"),  # plain http off this machine
    (KEY, "ftp://127.0.0.1/api/query"),
])
def test_a_key_and_a_safe_base_url_are_required(monkeypatch, key, base):
    monkeypatch.delenv("TRIBUTARY_API_KEY", raising=False)
    monkeypatch.delenv("TRIBUTARY_BASE_URL", raising=False)
    with pytest.raises(ValueError) as e:
        tc.Tributary(key, base)
    assert KEY not in str(e.value)


def test_the_key_and_url_from_the_environment(monkeypatch, api):
    monkeypatch.setenv("TRIBUTARY_API_KEY", KEY)
    monkeypatch.setenv("TRIBUTARY_BASE_URL", api.url + "/")
    api.answer("/v1/companies/search", (200, {}, {"results": [{"company_id": "0001045810"}]}))
    client = tc.Tributary()
    assert client.base_url == api.url
    assert client.search("NVDA") == [{"company_id": "0001045810"}]
    assert api.requests[0]["query"] == {"q": ["NVDA"], "limit": ["5"]}
    monkeypatch.delenv("TRIBUTARY_BASE_URL")
    assert tc.Tributary().base_url == tc.DEFAULT_BASE_URL
