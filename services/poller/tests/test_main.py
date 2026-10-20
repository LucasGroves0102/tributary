from datetime import datetime
from pathlib import Path

import httpx
from prometheus_client import REGISTRY

from poller.edgar import Filing
from poller.main import SeenSet, new_filings, poll_once

T0 = datetime.fromisoformat("2026-10-16T12:00:00-04:00")


def filing(acc: str, cik: str) -> Filing:
    return Filing(acc, cik, "Co", "Issuer", "8-K", T0, "u")


def test_new_filings_skips_seen():
    seen = SeenSet()
    batch = [filing("a", "1"), filing("a", "2")]
    assert new_filings(batch, seen) == batch
    assert new_filings(batch + [filing("b", "1")], seen) == [filing("b", "1")]


def test_seen_set_is_bounded():
    seen = SeenSet(maxlen=2)
    for k in [("a", "1"), ("b", "1"), ("c", "1")]:
        seen.add_if_new(k)
    assert seen.add_if_new(("a", "1"))  # evicted, so new again
    assert not seen.add_if_new(("c", "1"))


class FakeProducer:
    def __init__(self):
        self.published = []

    def publish(self, filing):
        self.published.append(filing)

    def flush(self, timeout=10.0):
        pass


def feed_client(status=200):
    content = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()
    return httpx.Client(transport=httpx.MockTransport(
        lambda _req: httpx.Response(status, content=content)))


def polls(result):
    return REGISTRY.get_sample_value("tributary_poller_polls_total", {"result": result}) or 0


def test_poll_once_publishes_new_filings_and_records_success():
    producer, seen = FakeProducer(), SeenSet()
    ok = polls("ok")
    poll_once(feed_client(), producer, seen, clock=lambda: 1_800_000_000.0)
    poll_once(feed_client(), producer, seen, clock=lambda: 1_800_000_030.0)
    assert len(producer.published) == 40  # the second poll finds nothing new
    assert polls("ok") == ok + 2
    assert REGISTRY.get_sample_value("tributary_poller_last_success_timestamp_seconds") \
        == 1_800_000_030.0


def test_poll_once_counts_edgar_errors_without_raising():
    producer = FakeProducer()
    http_errors = polls("http_error")
    poll_once(feed_client(status=403), producer, SeenSet())
    assert polls("http_error") == http_errors + 1
    assert producer.published == []
