"""doc-fetcher-bodies (migration 150): bodies missing from the store are found and re-fetched only
when the SEC's copy is still the stored body. Needs TEST_DATABASE_URL; every test is rolled back."""

import os

import httpx
import pytest

from doc_fetcher import bodies
from doc_fetcher.crawl_fetch import download
from tributary_bodies import Body, Store

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

PAGES = {f"https://sec/{n}": f"<html>{n}</html>".encode() for n in ("a", "b", "c", "x")}


@pytest.fixture
def conn():
    with psycopg.connect(DSN, autocommit=True) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE bronze.filing_documents CASCADE")
        yield c


def row(conn, store: Store, acc: str, page: str, *, keep: bool = True,
        exhibit: str | None = None) -> Body:
    body = Body.of(PAGES[f"https://sec/{page}"])
    if keep:
        store.put(body)
    if exhibit is None:
        conn.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
            " url, content_sha256, size_bytes, body_bytes) VALUES (%s, '1', '8-K', '2026-01-01',"
            " 'd', %s, %s, %s, %s)",
            (acc, f"https://sec/{page}", body.sha256, body.size, len(body.data)))
    else:
        conn.execute(
            "INSERT INTO bronze.filing_exhibits (accession_no, exhibit_type, document_name, url,"
            " content_sha256, size_bytes, body_bytes) VALUES (%s, %s, 'x', %s, %s, %s, %s)",
            (acc, exhibit, f"https://sec/{page}", body.sha256, body.size, len(body.data)))
    return body


def fetcher(requests: list[str], pages: dict[str, bytes]):
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        body = pages.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    return lambda url: download(client, url, bodies.MAX_BYTES)


def test_missing_bodies_are_counted_then_refetched(conn, tmp_path):
    store = Store(tmp_path)
    row(conn, store, "1", "a")                              # here
    b = row(conn, store, "2", "b", keep=False)              # gone
    row(conn, store, "2", "b", keep=False, exhibit="EX-99.1")  # the same body: one request
    row(conn, store, "3", "c", keep=False)                  # changed at the SEC since
    row(conn, store, "4", "x", keep=False)                  # gone from the SEC
    counted = bodies.run(conn, store, None)
    assert (counted.checked, counted.missing, counted.fetched) == (5, 3, 0)

    requests: list[str] = []
    changed = {**PAGES, "https://sec/c": b"<html>c, amended</html>"}
    del changed["https://sec/x"]
    result = bodies.run(conn, store, fetcher(requests, changed))
    assert (result.missing, result.fetched, result.changed, result.errors) == (3, 1, ["3"], ["4"])
    assert sorted(requests) == ["https://sec/b", "https://sec/c", "https://sec/x"]
    assert store.read(b.sha256) == PAGES["https://sec/b"]
    assert bodies.run(conn, store, None).missing == 2  # c and x stay missing, reported


def test_limit_caps_the_downloads(conn, tmp_path):
    store = Store(tmp_path)
    for acc, page in (("1", "a"), ("2", "b"), ("3", "c")):
        row(conn, store, acc, page, keep=False)
    requests: list[str] = []
    assert bodies.run(conn, store, fetcher(requests, PAGES), limit=2).fetched == 2
    assert len(requests) == 2
