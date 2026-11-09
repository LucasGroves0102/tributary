"""The live consumer's coverage (H10): tracked companies, then every operating company's new
filings from the feed, 8-Ks filtered by item from the index page. And a filing is stored whole or
not at all, its event owed until Kafka confirms it (review B2)."""

import contextlib
import gzip
import json
import os
from pathlib import Path

import httpx
import pytest

from doc_fetcher import crawl_db, db
from doc_fetcher.index import parse_items
from doc_fetcher.main import MATERIAL_8K_ITEMS, Config, Coverage, handle, sweep

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
    """Records each statement as "VERB table", and whether it ran inside a transaction."""

    def __init__(self):
        self.statements: list[str] = []
        self.in_tx: list[str] = []
        self._depth = 0

    def execute(self, sql, params=()):
        words = sql.split()
        statement = words[0] + " " + words[2]
        self.statements.append(statement)
        if self._depth:
            self.in_tx.append(statement)
        return FakeResult()

    @contextlib.contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1


class FakeMessage:
    def __init__(self, topic):
        self._topic = topic

    def topic(self):
        return self._topic


class FakeProducer:
    """Confirms every event at flush, unless `fail` (Kafka down: nothing confirmed)."""

    def __init__(self, fail: bool = False):
        self.events: list[dict] = []
        self.fail = fail
        self._pending: list = []

    def produce(self, topic, key, value, on_delivery=None):
        self.events.append({"topic": topic, **json.loads(value)})
        self._pending.append((topic, on_delivery))

    def flush(self, timeout=None):
        for topic, on_delivery in self._pending:
            if on_delivery is not None:
                on_delivery("broker down" if self.fail else None, FakeMessage(topic))
        self._pending = []
        return 0


def client(requests: list[str], failing: str | None = None) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requests.append(name)
        if name == failing:
            return httpx.Response(503)
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


def test_the_document_its_exhibits_and_the_owed_event_commit_together():
    conn, producer = FakeConn(), FakeProducer()
    assert handle(conn, client([]), producer, event()) is True
    assert conn.in_tx == ["INSERT bronze.filing_documents", "INSERT bronze.filing_exhibits",
                          "INSERT bronze.filing_exhibits", "INSERT ops.document_outbox"]
    # confirmed by Kafka: the outbox row goes, after the transaction
    assert conn.statements[-1] == "DELETE ops.document_outbox"


def test_a_failed_exhibit_fetch_stores_nothing():
    """Before: the document was committed, the exhibit failed, no event was sent, and the crawl
    later marked the stored filing published: never parsed."""
    conn, producer = FakeConn(), FakeProducer()
    with pytest.raises(httpx.HTTPStatusError):
        handle(conn, client([], failing="q2fy27cfocommentary.htm"), producer, event())
    assert not any(s.startswith("INSERT") for s in conn.statements)
    assert producer.events == []


def test_an_unconfirmed_event_stays_owed():
    conn, producer = FakeConn(), FakeProducer(fail=True)
    assert handle(conn, client([]), producer, event()) is True  # stored; the offset can move on
    assert "INSERT ops.document_outbox" in conn.in_tx
    assert "DELETE ops.document_outbox" not in conn.statements  # the sweep sends it again


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


DSN = os.environ.get("TEST_DATABASE_URL")


def stored_document(conn, acc: str, target: str | None = "claimed") -> None:
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed,"
        " document_name, url, content_gzip, content_sha256, size_bytes) VALUES"
        " (%s, %s, '8-K', '2026-11-09', 'd.htm', 'https://sec/d.htm', %s, 'abc', 4)",
        (acc, NVDA, gzip.compress(b"<h/>")))
    if target:
        conn.execute(
            "INSERT INTO ops.doc_crawl_targets (accession_no, cik, company_id, form,"
            " filing_date, priority, status, claimed_at) VALUES (%s, %s, %s, '8-K',"
            " '2026-11-09', 0, %s, now())", (acc, NVDA, NVDA, target))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
def test_owed_events_are_republished_and_the_crawl_never_sends_them_again():
    """Pre-ship review: the crawl left a filing the live side owed an event for unpublished and
    sent documents.backfill for it too, so it was parsed twice. The live outbox announces it."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as conn, conn.transaction(force_rollback=True):
        for acc in ("tm-owed", "tm-paid"):
            stored_document(conn, acc)
        db.owe_event(conn, "tm-owed")  # stored by the live consumer, event not confirmed yet
        conn.execute("UPDATE ops.document_outbox SET queued_at = now() - interval '5 minutes'")

        # the crawl finds both stored: the live side's events announce both, owed or sent
        crawl_db.mark_already_stored(conn, ["tm-owed", "tm-paid"])
        published = dict(conn.execute(
            "SELECT accession_no, published_at IS NOT NULL FROM ops.doc_crawl_targets"
            " WHERE accession_no LIKE 'tm-%'").fetchall())
        assert published == {"tm-owed": True, "tm-paid": True}
        assert crawl_db.unpublished(conn, 10) == []

        down, up = FakeProducer(fail=True), FakeProducer()
        assert sweep(conn, down) == 0
        assert conn.execute("SELECT attempts FROM ops.document_outbox").fetchone() == (1,)
        assert sweep(conn, up) == 1
        assert up.events == [{"topic": "documents.fetched", "accession_no": "tm-owed",
                              "cik": NVDA, "form": "8-K", "document_name": "d.htm",
                              "url": "https://sec/d.htm", "content_sha256": "abc",
                              "size_bytes": 4}]
        assert conn.execute("SELECT count(*) FROM ops.document_outbox").fetchone() == (0,)
        # a row just written is left to the send in progress
        db.owe_event(conn, "tm-paid")
        assert db.owed_events(conn, 10) == []


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
def test_a_crawl_target_the_live_side_also_stored_is_announced_once():
    """The race: the crawl stored the filing and marked it done, and the live consumer stored it
    too and owes its event. The crawl's publish skips it (marked published, nothing sent); a done
    target with no owed event is still sent as documents.backfill."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as conn, conn.transaction(force_rollback=True):
        for acc in ("tm-race", "tm-crawl"):
            stored_document(conn, acc, target="done")
        db.owe_event(conn, "tm-race")
        assert [e["accession_no"] for e in crawl_db.unpublished(conn, 10)] == ["tm-crawl"]
        published = dict(conn.execute(
            "SELECT accession_no, published_at IS NOT NULL FROM ops.doc_crawl_targets"
            " WHERE accession_no LIKE 'tm-%'").fetchall())
        assert published == {"tm-race": True, "tm-crawl": False}  # sent on delivery only
        assert [e["accession_no"] for e in crawl_db.unpublished(conn, 10)] == ["tm-crawl"]


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
def test_owed_rows_whose_document_is_gone_never_block_the_outbox():
    """Pre-ship review: owed_events took the 500 oldest outbox rows before joining documents, so
    500 rows whose documents were deleted would have held every other event back for good. The
    oldest are taken among rows with a document, and the orphans are cleared."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as conn, conn.transaction(force_rollback=True):
        for i in range(3):
            db.owe_event(conn, f"tm-gone-{i}")
        stored_document(conn, "tm-here", target=None)
        db.owe_event(conn, "tm-here")
        conn.execute("UPDATE ops.document_outbox SET queued_at = now() - interval '5 minutes'"
                     " - CASE WHEN accession_no LIKE 'tm-gone%' THEN interval '1 hour'"
                     "        ELSE interval '0' END")  # the orphans are the oldest
        assert [e["accession_no"] for e in db.owed_events(conn, 2)] == ["tm-here"]
        assert conn.execute("SELECT array_agg(accession_no) FROM ops.document_outbox"
                            ).fetchone() == (["tm-here"],)
        up = FakeProducer()
        assert sweep(conn, up) == 1 and [e["accession_no"] for e in up.events] == ["tm-here"]
