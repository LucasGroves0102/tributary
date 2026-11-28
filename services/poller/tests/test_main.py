from datetime import datetime, timedelta

import httpx
from prometheus_client import REGISTRY

from poller.edgar import Filing, parse_page
from poller.main import FeedState, SeenSet, WalkConfig, new_filings, poll_once

T0 = datetime.fromisoformat("2026-10-16T12:00:00-04:00")
TEN = WalkConfig(page_size=10)  # the saved page's 40 entries as four pages


def filing(acc: str, cik: str) -> Filing:
    return Filing(acc, cik, "Co", "Issuer", "8-K", T0, "u")


def entries(feed) -> list[Filing]:
    """The saved page's 40 filings, newest first (index = position in the feed)."""
    return parse_page(feed().page(0, 100)).filings


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
    assert ("c", "1") in seen


class FakeProducer:
    def __init__(self):
        self.published = []

    def publish(self, filing):
        self.published.append(filing)

    def flush(self, timeout=10.0):
        pass


def polls(result):
    return REGISTRY.get_sample_value("tributary_poller_polls_total", {"result": result}) or 0


def unsettled():
    return REGISTRY.get_sample_value("tributary_poller_unsettled_polls_total") or 0


def seen_state(feed, without=(), newest=None) -> FeedState:
    """As after a poll that saw every saved entry except `without`."""
    state = FeedState()
    for i, f in enumerate(entries(feed)):
        if i not in without:
            state.seen.add_if_new(f.key)
    state.newest = newest or entries(feed)[0].filed_at
    return state


def test_poll_once_publishes_new_filings_and_records_success(feed):
    producer, state, server = FakeProducer(), FeedState(), feed()
    ok = polls("ok")
    poll_once(server.client(), producer, state, clock=lambda: 1_800_000_000.0)
    poll_once(server.client(), producer, state, clock=lambda: 1_800_000_030.0)
    assert len(producer.published) == 40  # the second poll finds nothing new
    assert producer.published == list(reversed(entries(feed)))  # oldest first
    assert polls("ok") == ok + 2
    assert REGISTRY.get_sample_value("tributary_poller_last_success_timestamp_seconds") \
        == 1_800_000_030.0
    # 40 entries where 100 were asked: the feed's end, so one page each
    assert len(server.requests) == 2 and "start" not in server.requests[0].params


def test_poll_once_counts_edgar_errors_without_raising(feed):
    producer = FakeProducer()
    http_errors = polls("http_error")
    poll_once(feed(fail={0: 403}).client(), producer, FeedState())
    assert polls("http_error") == http_errors + 1
    assert producer.published == []


def test_a_page_that_is_not_xml_is_a_failed_poll_not_a_crash():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda _req: httpx.Response(200, content=b"<html>Service Unavailable")))
    failed = polls("failed")
    assert poll_once(client, FakeProducer(), FeedState()) == []
    assert polls("failed") == failed + 1


def test_a_burst_deeper_than_a_page_is_walked_back_to_the_last_poll(feed):
    server, state, producer = feed(listed=range(30, 40)), FeedState(), FakeProducer()
    poll_once(server.client(), producer, state, config=TEN)  # the feed as it stood earlier
    assert len(producer.published) == 10
    server.listed = list(range(40))  # 30 new filer rows since: three pages' worth
    new = poll_once(server.client(), producer, state, config=TEN)
    assert new == list(reversed(entries(feed)[:30]))  # oldest first
    # three new pages, then the page the last poll saw
    assert [u.params.get("start") for u in server.requests[1:]] == [None, "10", "20", "30"]
    assert server.requests[-1].params["count"] == "10"
    assert state.newest == entries(feed)[0].filed_at


def test_one_page_misses_a_burst_and_says_so(feed):
    """What the poller did before it paged: the 20 rows below the first page were left to the
    nightly check. A walk cut short is counted."""
    server, state = feed(listed=range(30, 40)), FeedState()
    poll_once(server.client(), FakeProducer(), state, config=TEN)
    server.listed = list(range(40))
    before = unsettled()
    new = poll_once(server.client(), FakeProducer(), state,
                    config=WalkConfig(page_size=10, max_pages=1))
    assert len(new) == 10
    assert unsettled() == before + 1
    assert state.newest == entries(feed)[0].filed_at  # advanced: the next poll doesn't loop


def test_a_quiet_poll_is_one_page(feed):
    server = feed()
    state = seen_state(feed)
    assert poll_once(server.client(), FakeProducer(), state, config=TEN) == []
    assert len(server.requests) == 1


def test_an_entry_listed_late_below_seen_ones_walks_deeper(feed):
    # entries 5 (21:37:37) and 15 (21:20:01) appeared after the entries around them were seen
    server = feed()
    state = seen_state(feed, without={5, 15})
    new = poll_once(server.client(), FakeProducer(), state, config=TEN)
    assert new == [entries(feed)[15], entries(feed)[5]]
    assert len(server.requests) == 3  # page 3 shows nothing new: settled


def test_the_walk_reaches_back_the_lookback_before_the_last_poll(feed):
    # entry 10 (21:21:15) heads page 2, listed after the last poll saw everything else
    state = seen_state(feed, without={10})
    server = feed()
    wide = WalkConfig(page_size=10, lookback=timedelta(minutes=30))
    assert poll_once(server.client(), FakeProducer(), state, config=wide) == [entries(feed)[10]]
    assert len(server.requests) == 2
    # 24 minutes behind the last poll's newest, past the default 2-minute lookback: that one is
    # the hourly recheck's
    state = seen_state(feed, without={10})
    assert poll_once(feed().client(), FakeProducer(), state, config=TEN) == []


def test_the_first_poll_walks_back_the_startup_lookback(feed):
    # nothing seen yet: back to 30 minutes before the feed's own time (21:48:40)
    server = feed()
    config = WalkConfig(page_size=10, startup_lookback=timedelta(minutes=30))
    new = poll_once(server.client(), FakeProducer(), FeedState(), config=config)
    assert len(new) == 20 and len(server.requests) == 2
    server = feed(updated="2026-10-16T21:40:00-04:00")
    new = poll_once(server.client(), FakeProducer(), FeedState(),
                    config=WalkConfig(page_size=10, startup_lookback=timedelta(minutes=1)))
    assert len(new) == 10 and len(server.requests) == 1


def test_a_failed_page_publishes_what_was_found_and_keeps_the_horizon(feed):
    last = entries(feed)[20].filed_at  # the last finished poll's newest: 21:00:18
    state = seen_state(feed, without=set(range(20)), newest=last)
    server, producer = feed(fail={10: 500}), FakeProducer()
    http_errors = polls("http_error")
    assert poll_once(server.client(), producer, state, config=TEN) == \
        list(reversed(entries(feed)[:10]))
    assert polls("http_error") == http_errors + 1
    assert state.newest == last
    server.fail = {}
    # the next poll walks over the gap the failure left (entries 10-19)
    assert poll_once(server.client(), producer, state, config=TEN) == \
        list(reversed(entries(feed)[10:20]))
    assert state.newest == entries(feed)[0].filed_at


def test_a_feed_that_stops_paging_ends_the_walk(feed):
    server = feed(paging=False)
    state = FeedState(newest=entries(feed)[0].filed_at)  # a restart's memory: nothing seen
    before = unsettled()
    new = poll_once(server.client(), FakeProducer(), state, config=TEN)
    assert len(new) == 10 and len(server.requests) == 2
    assert unsettled() == before + 1


def test_max_pages_bounds_the_walk(feed):
    state = FeedState(newest=entries(feed)[39].filed_at - timedelta(hours=1))
    server = feed()
    new = poll_once(server.client(), FakeProducer(), state,
                    config=WalkConfig(page_size=10, max_pages=2))
    assert len(new) == 20 and len(server.requests) == 2
    assert state.newest == entries(feed)[0].filed_at
