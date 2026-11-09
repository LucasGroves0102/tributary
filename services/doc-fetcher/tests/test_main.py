"""The live consumer's coverage (H10): tracked companies, then every operating company's new
filings from the feed, 8-Ks filtered by item from the index page."""

import json
from pathlib import Path

import httpx

from doc_fetcher.index import parse_items
from doc_fetcher.main import MATERIAL_8K_ITEMS, Config, Coverage, handle

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_8K = (FIXTURES / "nvda-8-K-index.htm").read_bytes()
NVDA = "0001045810"
OTHER = "0000320193"


def event(cik: str = NVDA, form: str = "8-K", **extra) -> dict:
    return {"accession_no": "0001045810-26-000073", "cik": cik, "form_type": form,
            "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/"
                   "0001045810-26-000073-index.htm", **extra}


def test_items_from_8k_index():
    assert parse_items(INDEX_8K) == {"2.02", "9.01"}
    assert parse_items((FIXTURES / "nvda-10-K-index.htm").read_bytes()) == frozenset()


def test_scope_tracked_takes_every_source():
    cov = Coverage(tracked=frozenset({NVDA}), operating=frozenset({NVDA, OTHER}))
    assert cov.scope(event(source="seed")) == "all"
    assert cov.scope(event(source="daily-index")) == "all"
    assert cov.scope(event(form="4")) is None


def test_scope_operating_takes_the_live_feed_only():
    cov = Coverage(tracked=frozenset(), operating=frozenset({OTHER}))
    assert cov.scope(event(cik=OTHER, form="10-Q")) == "live"           # no source: the feed
    assert cov.scope(event(cik=OTHER, form="10-K", source="feed")) == "live"
    assert cov.scope(event(cik=OTHER, form="10-Q", source="daily-index")) is None  # the crawl's
    assert cov.scope(event(cik=OTHER, form="10-K/A")) is None
    assert cov.scope(event(cik="0000000001", form="10-Q")) is None      # not an operating company


def test_config_from_env():
    assert Config.from_env({}) == Config(True, frozenset(MATERIAL_8K_ITEMS))
    assert Config.from_env({"FETCH_OPERATING": "false"}).operating is False
    assert Config.from_env({"FETCH_8K_ITEMS": "all"}).eight_k_items is None
    assert Config.from_env({"FETCH_8K_ITEMS": "2.02, 7.01"}).eight_k_items == {"2.02", "7.01"}


class FakeResult:
    def fetchone(self):
        return None


class FakeConn:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql.split()[0] + " " + sql.split()[2])
        return FakeResult()


class FakeProducer:
    def __init__(self):
        self.events: list[dict] = []

    def produce(self, topic, key, value, on_delivery=None):
        self.events.append({"topic": topic, **json.loads(value)})

    def flush(self, timeout=None):
        return 0


def client(requests: list[str]) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path.rsplit("/", 1)[-1])
        if request.url.path.endswith("-index.htm"):
            return httpx.Response(200, content=INDEX_8K)
        return httpx.Response(200, content=b"<html>press release</html>")
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_material_8k_is_fetched_with_its_exhibits():
    requests: list[str] = []
    conn, producer = FakeConn(), FakeProducer()
    assert handle(conn, client(requests), producer, event(),
                  eight_k_items=frozenset(MATERIAL_8K_ITEMS)) is True
    assert requests == ["0001045810-26-000073-index.htm", "nvda-20260826.htm",
                        "q2fy27pr.htm", "q2fy27cfocommentary.htm"]
    assert [e["topic"] for e in producer.events] == ["documents.fetched"]
    assert conn.statements.count("INSERT bronze.filing_exhibits") == 2


def test_8k_without_a_fetched_item_stops_at_the_index():
    requests: list[str] = []
    conn, producer = FakeConn(), FakeProducer()
    assert handle(conn, client(requests), producer, event(),
                  eight_k_items=frozenset({"5.07"})) is False
    assert requests == ["0001045810-26-000073-index.htm"]
    assert producer.events == []
    assert not any(s.startswith("INSERT") for s in conn.statements)


def test_tracked_8k_ignores_items():
    requests: list[str] = []
    producer = FakeProducer()
    assert handle(FakeConn(), client(requests), producer, event(), eight_k_items=None) is True
    assert len(producer.events) == 1
