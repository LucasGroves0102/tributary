"""doc-crawler without a database, Kafka or the SEC: saved EDGAR pages behind httpx.MockTransport,
an in-memory queue and producer."""

import gzip
import hashlib
import json
import threading
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from doc_fetcher.crawl import Config, Crawler, classify, years_before
from doc_fetcher.crawl_db import Control
from doc_fetcher.crawl_fetch import Fetched, Target, Unusable, download, fetch_target, needs_index
from doc_fetcher.db import Blob
from doc_fetcher.index import FilingIndex

FIXTURES = Path(__file__).parent / "fixtures"
PARSER_FIXTURES = Path(__file__).parents[2] / "parser" / "tests" / "fixtures"
INDEX_8K = (FIXTURES / "nvda-8-K-index.htm").read_bytes()
DOC_8K = gzip.decompress((PARSER_FIXTURES / "nvda-8-K.htm.gz").read_bytes())
NVDA = "0001045810"
ACC_8K = "0001045810-26-000073"
BASE = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/"


def target(acc="a1", form="10-K", primary="doc.htm", attempts=0, filed=date(2026, 2, 25)):
    return Target(acc, NVDA, form, filed, None, primary, attempts)


def mock_client(pages: dict[str, bytes], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        body = pages.get(str(request.url))
        return httpx.Response(404) if body is None else httpx.Response(200, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


# fetching ------------------------------------------------------------------------------------

def test_10k_fetches_the_primary_document_directly():
    calls: list[str] = []
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"
    client = mock_client({url: DOC_8K}, calls)
    t = Target("0001045810-26-000021", NVDA, "10-K", date(2026, 2, 25), date(2026, 1, 25),
               "nvda-20260125.htm")
    fetched = fetch_target(client, t, 10**8)
    assert calls == [url]
    assert fetched.requests == 1
    assert fetched.index == FilingIndex(date(2026, 2, 25), date(2026, 1, 25),
                                        "nvda-20260125.htm", url)
    # stored exactly like doc-fetcher's gzip.compress: both parsers gunzip it
    assert gzip.decompress(fetched.document.content_gzip) == DOC_8K
    assert fetched.document.sha256 == hashlib.sha256(DOC_8K).hexdigest()
    assert fetched.document.size == len(DOC_8K)
    assert fetched.exhibits == []


def test_8k_reads_the_index_then_document_and_exhibits():
    calls: list[str] = []
    pages = {
        f"{BASE}{ACC_8K}-index.htm": INDEX_8K,
        f"{BASE}nvda-20260826.htm": DOC_8K,
        f"{BASE}q2fy27pr.htm": b"<html>press release</html>",
        f"{BASE}q2fy27cfocommentary.htm": b"<html>cfo commentary</html>",
    }
    fetched = fetch_target(mock_client(pages, calls), target(ACC_8K, "8-K", None), 10**8)
    assert calls == list(pages)
    assert fetched.requests == 4
    assert fetched.index.document_name == "nvda-20260826.htm"
    assert [(e.exhibit_type, gzip.decompress(b.content_gzip)) for e, b in fetched.exhibits] == [
        ("EX-99.1", b"<html>press release</html>"),
        ("EX-99.2", b"<html>cfo commentary</html>")]
    assert fetched.files == 3
    assert fetched.raw_bytes == len(DOC_8K) + 26 + 27
    assert fetched.stored_bytes == sum(len(b.content_gzip) for b in
                                       [fetched.document, *(b for _, b in fetched.exhibits)])


def test_missing_exhibit_fails_the_filing_so_it_is_retried():
    calls: list[str] = []
    pages = {f"{BASE}{ACC_8K}-index.htm": INDEX_8K, f"{BASE}nvda-20260826.htm": DOC_8K}
    with pytest.raises(httpx.HTTPStatusError):
        fetch_target(mock_client(pages, calls), target(ACC_8K, "8-K", None), 10**8)


def test_index_without_the_form_is_unusable():
    calls: list[str] = []
    pages = {f"{BASE}{ACC_8K}-index.htm": INDEX_8K}
    with pytest.raises(Unusable, match="no 10-K document"):
        fetch_target(mock_client(pages, calls), target(ACC_8K, "10-K", None), 10**8)


def test_download_refuses_large_and_empty_files():
    calls: list[str] = []
    client = mock_client({"https://x/big": b"x" * 200_000, "https://x/empty": b""}, calls)
    with pytest.raises(Unusable, match="over"):
        download(client, "https://x/big", 100_000)
    with pytest.raises(Unusable, match="empty"):
        download(client, "https://x/empty", 100_000)


@pytest.mark.parametrize(("form", "primary", "expected"), [
    ("10-K", "a.htm", False), ("10-Q/A", "a.HTML", False), ("8-K", "a.htm", True),
    ("10-K", None, True), ("10-K", "a.txt", True), ("10-K", "xsl/a.htm", True),
])
def test_needs_index(form, primary, expected):
    assert needs_index(target(form=form, primary=primary)) is expected


def test_classify():
    req = httpx.Request("GET", "https://www.sec.gov/x")

    def status(code: int) -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code))
    assert classify(status(404)) == "failed"
    assert classify(status(403)) == "retry"   # throttle, or a missing file: retried, then failed
    assert classify(status(503)) == "retry"
    assert classify(httpx.ConnectError("x")) == "retry"
    assert classify(Unusable("x")) == "skipped"


def test_config_from_env():
    c = Config.from_env({"CRAWL_8K_ITEMS": "2.02, 5.02", "CRAWL_MIN_FREE_GB": "75",
                         "CRAWL_BATCH": "8"})
    assert c.eight_k_items == ("2.02", "5.02")
    assert c.min_free_bytes == 75 * 10**9
    assert c.batch == 8
    assert Config.from_env({"CRAWL_8K_ITEMS": "all"}).eight_k_items is None
    assert Config.from_env({}).eight_k_items == ("2.02", "7.01", "8.01")


def test_years_before_handles_leap_days():
    assert years_before(date(2028, 2, 29), 5) == date(2023, 2, 28)


# the crawl loop ------------------------------------------------------------------------------

class FakeQueue:
    """ops.doc_crawl_targets in memory, with the same claim order as crawl_db.claim."""

    def __init__(self, targets: list[Target], stored: set[str] = frozenset()):
        self.targets = {t.accession_no: t for t in targets}
        self.status = dict.fromkeys(self.targets, "pending")
        self.stored_elsewhere = set(stored)
        self.stored: dict[str, Fetched] = {}
        self.published: set[str] = set()
        self.errors: dict[str, str] = {}
        self.delays: dict[str, timedelta] = {}
        self.released: list[str] = []
        self.plans: list[tuple[date, list[str] | None]] = []
        self.paused = False
        self.states: list[tuple[str, int | None]] = []

    def plan(self, since, eight_k_items):
        self.plans.append((since, eight_k_items))
        return 0

    def claim(self, limit, lease):
        pending = sorted((a for a, s in self.status.items() if s == "pending"),
                         key=lambda a: (-self.targets[a].filing_date.toordinal(), a))[:limit]
        for a in pending:
            self.status[a] = "claimed"
        return [self.targets[a] for a in pending]

    def already_stored(self, accessions):
        return self.stored_elsewhere & set(accessions)

    def mark_already_stored(self, accessions):
        for a in accessions:
            self.status[a] = "done"
            self.published.add(a)

    def store(self, target, fetched):
        self.stored[target.accession_no] = fetched
        self.status[target.accession_no] = "done"

    def finish(self, accession_no, status, error):
        self.status[accession_no] = status
        self.errors[accession_no] = error

    def retry_later(self, accession_no, error, delay):
        t = self.targets[accession_no]
        self.targets[accession_no] = replace(t, attempts=t.attempts + 1)
        self.status[accession_no] = "pending"
        self.errors[accession_no] = error
        self.delays[accession_no] = delay

    def release(self, accessions):
        self.released += accessions
        for a in accessions:
            self.status[a] = "pending"

    def unpublished(self, limit):
        return [{"accession_no": a, "cik": NVDA, "form": self.targets[a].form,
                 "document_name": f.index.document_name, "url": f.index.url,
                 "content_sha256": f.document.sha256, "size_bytes": f.document.size}
                for a, f in self.stored.items() if a not in self.published][:limit]

    def mark_published(self, accessions):
        self.published |= set(accessions)

    def remaining(self):
        return sum(s in ("pending", "claimed") for s in self.status.values())

    def control(self):
        return Control(paused=self.paused)

    def report(self, state, disk_free, min_free):
        self.states.append((state, disk_free))


class FakeMessage:
    def __init__(self, topic):
        self._topic = topic

    def topic(self):
        return self._topic


class FakeProducer:
    def __init__(self, fail: set[str] = frozenset()):
        self.fail = fail
        self.pending: list = []
        self.sent: list[tuple[str, bytes, dict]] = []

    def produce(self, topic, value, key, on_delivery):
        self.pending.append((topic, key, json.loads(value), on_delivery))

    def flush(self, timeout):
        for topic, key, value, cb in self.pending:
            err = "broker down" if value["accession_no"] in self.fail else None
            if err is None:
                self.sent.append((topic, key, value))
            cb(err, FakeMessage(topic))
        self.pending = []
        return 0


def fetched(t: Target) -> Fetched:
    doc = f"<html>{t.accession_no}</html>".encode()
    return Fetched(FilingIndex(t.filing_date, None, "doc.htm", f"https://sec/{t.accession_no}"),
                   Blob.of(doc), [], 1)


def crawler(queue, fetch=fetched, producer=None, free=10**12, **config):
    config = {"workers": 2, "batch": 3, **config}
    return Crawler(queue, fetch, producer or FakeProducer(), Config(**config),
                   free_bytes=lambda: free, today=lambda: date(2026, 10, 20),
                   clock=lambda: 0.0, wall=lambda: 1.0, stop=threading.Event())


def days(n):
    return date(2026, 10, 1) - timedelta(days=n)


def test_crawls_in_batches_most_recent_first_and_publishes():
    queue = FakeQueue([target(f"a{i}", filed=days(i)) for i in range(5)])
    producer = FakeProducer()
    c = crawler(queue, producer=producer)
    assert c.round() == 0
    assert set(queue.stored) == {"a0", "a1", "a2"}
    assert c.round() == 0
    assert set(queue.stored) == {"a0", "a1", "a2", "a3", "a4"}
    assert [v["accession_no"] for _, _, v in producer.sent] == ["a0", "a1", "a2", "a3", "a4"]
    topic, key, event = producer.sent[0]
    assert (topic, key) == ("documents.backfill", NVDA.encode())
    # documents.fetched's shape plus source
    assert set(event) == {"accession_no", "cik", "form", "document_name", "url",
                          "content_sha256", "size_bytes", "source"}
    assert event["source"] == "crawl"
    assert queue.published == set(queue.stored)
    assert c.round() == c.config.idle_seconds  # nothing left
    assert queue.states[-1][0] == "idle"


def test_plans_on_start_then_every_plan_hours():
    queue = FakeQueue([])
    now = [0.0]
    c = crawler(queue, eight_k_items=("2.02",), plan_hours=6)
    c.clock = lambda: now[0]
    c.round()
    c.round()
    assert queue.plans == [(date(2021, 10, 20), ["2.02"])]
    now[0] = 6 * 3600
    c.round()
    assert len(queue.plans) == 2
    every_8k = FakeQueue([])
    crawler(every_8k, eight_k_items=None).round()
    assert every_8k.plans == [(date(2021, 10, 20), None)]


def test_failed_delivery_is_published_again_next_round():
    queue = FakeQueue([target("a1"), target("a2")])
    producer = FakeProducer(fail={"a2"})
    c = crawler(queue, producer=producer)
    c.round()
    assert queue.published == {"a1"}
    producer.fail = set()
    c.round()
    assert queue.published == {"a1", "a2"}


def test_failures_retry_with_backoff_then_give_up():
    req = httpx.Request("GET", "https://www.sec.gov/x")

    def fetch(t):
        if t.accession_no == "gone":
            raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404))
        if t.accession_no == "huge":
            raise Unusable("over 100 MB")
        if t.accession_no.startswith("flaky"):
            raise httpx.ReadTimeout("slow")
        return fetched(t)

    queue = FakeQueue([target("gone"), target("huge"), target("flaky", attempts=1),
                       target("flaky-4th", attempts=3)])
    crawler(queue, fetch=fetch, batch=4, max_attempts=4).round()
    assert queue.status == {"gone": "failed", "huge": "skipped", "flaky": "pending",
                            "flaky-4th": "failed"}
    assert queue.delays == {"flaky": timedelta(minutes=20)}  # 10 min * 2 ** attempts
    assert "ReadTimeout" in queue.errors["flaky"]


def test_already_stored_filings_are_not_fetched():
    calls = []
    queue = FakeQueue([target("a1"), target("a2")], stored={"a1"})
    c = crawler(queue, fetch=lambda t: calls.append(t.accession_no) or fetched(t))
    c.round()
    assert calls == ["a2"]
    assert queue.status == {"a1": "done", "a2": "done"}


def test_disk_guard_stops_claiming():
    queue = FakeQueue([target("a1")])
    c = crawler(queue, free=49 * 10**9, min_free_bytes=50 * 10**9, pause_seconds=300)
    assert c.round() == 300
    assert queue.status == {"a1": "pending"}
    assert queue.plans == []
    assert queue.states == [("disk_low", 49 * 10**9)]
    c.free_bytes = lambda: 51 * 10**9
    assert c.round() == 0
    assert queue.status == {"a1": "done"}


def test_operator_pause():
    queue = FakeQueue([target("a1")])
    queue.paused = True
    c = crawler(queue, pause_seconds=60)
    assert c.round() == 60
    assert queue.status == {"a1": "pending"}
    assert queue.states == [("paused", None)]


def test_backs_off_when_every_fetch_fails():
    queue = FakeQueue([target(f"a{i}") for i in range(5)])

    def down(t):
        raise httpx.ConnectError("no route")
    c = crawler(queue, fetch=down, batch=5, pause_seconds=300)
    assert c.round() == 300
    assert queue.states[-1][0] == "erroring"
    assert set(queue.status.values()) == {"pending"}


def test_stop_mid_batch_releases_what_was_not_attempted():
    queue = FakeQueue([target(f"a{i}", filed=days(i)) for i in range(6)])
    c = crawler(queue, workers=1, batch=6)

    def fetch(t):
        c.stop.set()
        return fetched(t)
    c.fetch = fetch
    c.round()
    done = {a for a, s in queue.status.items() if s == "done"}
    assert "a0" in done
    assert done | set(queue.released) == set(queue.targets)  # nothing lost, nothing left claimed
    assert "claimed" not in queue.status.values()


def test_resume_after_crash_continues_where_it_stopped():
    # a crashed run left a3 claimed; a restarted crawler takes the rest (claim expiry is SQL)
    queue = FakeQueue([target(f"a{i}", filed=days(i)) for i in range(4)])
    crawler(queue, batch=2).round()
    assert set(queue.stored) == {"a0", "a1"}
    crawler(queue, batch=2).round()
    assert set(queue.stored) == {"a0", "a1", "a2", "a3"}
