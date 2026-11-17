"""Client ID metadata documents (query_api/client_metadata.py): which URLs are fetched at all,
that a name answering a private address is never connected to, that redirects, other content,
oversized documents and slow servers are refused, and what a document must say. The resolver and
the connection are injected: nothing here touches the network."""

import json
import socket
import threading
import time

import pytest

from query_api.client_metadata import (
    MAX_DOCUMENT,
    DocumentRefused,
    HttpsDocumentFetcher,
    clean_name,
    document_url,
    max_age_of,
    parse_document,
)
from query_api.oauth import redirect_ok

URL = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CB = "https://claude.ai/api/mcp/auth_callback"


def resolver(address="160.79.104.10"):
    calls = []

    def resolve(host, port, type=None):
        calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    resolve.calls = calls
    return resolve


class FakeResponse:
    def __init__(self, status, body, headers):
        self.status, self._body, self._headers = status, body, headers

    def getheader(self, name):
        return self._headers.get(name)

    def read(self, n):
        return self._body[:n]


class FakeConn:
    def __init__(self, status=200, body=b"{}", headers=None, hang=False):
        self.status, self.body = status, body
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}
        self.hang, self.requests, self.sock = hang, [], None
        self.released = threading.Event()

    def connect(self):
        if self.hang:
            self.released.wait(5)

    def request(self, method, path, headers=None):
        self.requests.append((method, path))

    def getresponse(self):
        return FakeResponse(self.status, self.body, self.headers)

    def close(self):
        self.released.set()


def opener(conn):
    opened = []

    def open_(target, address, timeout):
        opened.append((target.host, address))
        return conn
    open_.opened = opened
    return open_


@pytest.mark.parametrize("url", [
    URL, "https://claude.ai/oauth/claude-code-client-metadata",
    "https://chatgpt.com/oauth/client.json", "https://chatgpt.com/oauth/abc123/client.json",
    "https://app.example.com/client.json?v=2"])
def test_document_urls_that_are_fetched(url):
    assert document_url(url)


@pytest.mark.parametrize("url", [
    "http://claude.ai/oauth/doc", "https://claude.ai", "https://claude.ai/",
    "https://claude.ai/doc#frag", "https://user@claude.ai/doc", "https://claude.ai:8443/doc",
    "https://127.0.0.1/doc", "https://[::1]/doc", "https://localhost/doc",
    "https://metadata.google.internal/doc", "https://api.svc.cluster.local/doc",
    "https://intranet/doc", "https://claude.ai/a/../doc", "https://claude.ai/./doc",
    "https://claude.ai/doc with space", "trc_" + "A" * 32, "", "https://" + "a" * 2050])
def test_document_urls_that_are_never_fetched(url):
    assert not document_url(url)


def test_a_name_answering_a_private_address_is_never_connected_to():
    for private in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "100.64.0.1"):
        conn = FakeConn(body=json.dumps({"client_id": URL}).encode())
        open_ = opener(conn)
        fetcher = HttpsDocumentFetcher(resolver(private), open_)
        with pytest.raises(DocumentRefused) as e:
            fetcher.fetch(URL)
        assert e.value.code == "private_address" and open_.opened == []


def test_a_good_document_is_fetched_from_the_address_checked():
    body = json.dumps({"client_id": URL, "client_name": "Claude",
                       "redirect_uris": [CB]}).encode()
    conn = FakeConn(body=body, headers={"Content-Type": "application/json; charset=utf-8",
                                        "Cache-Control": "public, max-age=3600"})
    resolve, open_ = resolver(), opener(conn)
    got = HttpsDocumentFetcher(resolve, open_).fetch(URL)
    assert got.body == body and got.max_age == 3600
    assert resolve.calls == ["claude.ai."]  # absolute: no search domains
    assert open_.opened == [("claude.ai", "160.79.104.10")]
    assert conn.requests == [("GET", "/oauth/mcp-oauth-client-metadata")]


@pytest.mark.parametrize("status, headers, body, code", [
    (302, {"Location": "http://10.0.0.1/"}, b"", "redirect"),
    (404, {"Content-Type": "application/json"}, b"{}", "status"),
    (200, {"Content-Type": "text/html"}, b"<html>", "content_type"),
    (200, {"Content-Type": "application/json"}, b"x" * (MAX_DOCUMENT + 1), "too_large")])
def test_redirects_other_content_and_big_documents_are_refused(status, headers, body, code):
    fetcher = HttpsDocumentFetcher(resolver(), opener(FakeConn(status, body, headers)))
    with pytest.raises(DocumentRefused) as e:
        fetcher.fetch(URL)
    assert e.value.code == code


def test_a_slow_server_is_cut_at_the_deadline():
    conn = FakeConn(hang=True)
    fetcher = HttpsDocumentFetcher(resolver(), opener(conn), total_timeout=0.3)
    start = time.monotonic()
    with pytest.raises(DocumentRefused) as e:
        fetcher.fetch(URL)
    assert e.value.code == "timeout" and time.monotonic() - start < 2


def test_a_document_must_name_itself_and_safe_redirects():
    good = {"client_id": URL, "client_name": "Claude", "redirect_uris": [CB, CB]}
    doc = parse_document(URL, json.dumps(good).encode(), redirect_ok)
    assert doc.name == "Claude" and doc.redirect_uris == (CB,)
    for bad, code in (({**good, "client_id": URL + "x"}, "client_id"),
                      ({**good, "redirect_uris": ["javascript:alert(1)"]}, "redirect_uris"),
                      ({**good, "redirect_uris": ["http://evil.example/cb"]}, "redirect_uris"),
                      ({**good, "redirect_uris": [CB] * 11}, "redirect_uris"),
                      ({k: v for k, v in good.items() if k != "redirect_uris"}, "redirect_uris")):
        with pytest.raises(DocumentRefused) as e:
            parse_document(URL, json.dumps(bad).encode(), redirect_ok)
        assert e.value.code == code
    with pytest.raises(DocumentRefused):
        parse_document(URL, b"\xff\xfe", redirect_ok)
    nameless = parse_document(URL, json.dumps({**good, "client_name": 5}).encode(), redirect_ok)
    assert nameless.name == "claude.ai"


def test_names_and_cache_headers():
    assert clean_name("  Claude‮  Code\x00 ", "x") == "Claude Code"
    assert clean_name("", "fallback") == "fallback" and clean_name(None, "f") == "f"
    assert len(clean_name("a" * 500, "x")) == 100
    assert max_age_of("public, max-age=600") == 600 and max_age_of("no-store") is None
    assert max_age_of(None) is None
