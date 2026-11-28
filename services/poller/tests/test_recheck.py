from datetime import date, datetime, timedelta

from poller.daily_index import EASTERN
from poller.edgar import parse_page
from poller.recheck import previous_weekday, run, walk, window_start


class FakeProducer:
    def __init__(self):
        self.published = []

    def publish(self, filing):
        self.published.append(filing)

    def flush(self, timeout=10.0):
        pass


def entries(feed):
    return parse_page(feed().page(0, 100)).filings  # newest first: 21:45:15 → 20:07:10


def at(hhmm: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-16T{hhmm}:00-04:00")


def test_walk_reads_pages_until_one_lies_before_the_window(feed):
    server = feed()
    listing = walk(server.client(), at("21:01"), page_size=10)
    # page 2 (21:21:15 → 21:00:43) reaches past 21:01; page 3 is wholly older: complete there
    assert listing.complete and listing.pages == 3
    assert len(listing.filings) == 30
    assert listing.reached_back_to == entries(feed)[29].filed_at


def test_a_feed_that_ends_before_the_window_is_incomplete(feed):
    listing = walk(feed().client(), at("19:00"), page_size=10)
    assert not listing.complete and listing.pages == 5  # the fifth page is empty: the end
    assert len(listing.filings) == 40 and listing.reached_back_to == at("20:07") + \
        timedelta(seconds=10)


def test_the_walk_stops_at_max_pages_or_a_feed_that_stops_paging(feed):
    listing = walk(feed().client(), at("19:00"), max_pages=2, page_size=10)
    assert (listing.complete, listing.pages, len(listing.filings)) == (False, 2, 20)
    listing = walk(feed(paging=False).client(), at("19:00"), page_size=10)
    assert (listing.complete, listing.pages, len(listing.filings)) == (False, 2, 10)


def test_run_republishes_only_what_bronze_lacks_as_recheck(feed):
    listed = entries(feed)
    have = {f.key for f in listed[1:]}  # bronze has every row but the newest one's issuer...
    have.discard(listed[25].key)        # ...and a row from 20:48:51
    asked = []

    def missing(filings):
        asked.extend(filings)
        return [f for f in filings if f.key not in have]

    producer = FakeProducer()
    result = run(feed().client(), producer, at("20:00"), at("22:00"), missing)
    assert [f.key for f in producer.published] == [listed[25].key, listed[0].key]  # oldest first
    assert all(f.source == "recheck" for f in producer.published)
    # the feed's own acceptance time and role, unlike the daily index's midnight and Unknown
    assert producer.published[1].filed_at == listed[0].filed_at
    assert producer.published[1].role == listed[0].role == "Reporting"
    assert result.listed == 40 and len(asked) == 40
    assert result.missing_accessions == 2 and result.listing.complete is False  # feed ended


def test_run_leaves_the_last_minutes_to_the_poller_and_older_days_to_the_nightly(feed):
    listed = entries(feed)
    producer = FakeProducer()
    # at 21:50 with 10 minutes' grace: 21:45:15 and 21:42:41 are the poller's; 20:07 is
    # before the window
    result = run(feed().client(), producer, at("20:08"), at("21:50"), lambda fs: list(fs))
    keys = [f.key for f in producer.published]
    assert listed[0].key not in keys and listed[3].key not in keys
    assert listed[39].key not in keys and listed[36].key not in keys
    assert listed[4].key in keys and listed[35].key in keys
    assert result.listed == 32


def test_previous_weekday():
    assert previous_weekday(date(2026, 11, 30)) == date(2026, 11, 27)  # Monday → Friday
    assert previous_weekday(date(2026, 11, 25)) == date(2026, 11, 24)
    assert previous_weekday(date(2026, 11, 29)) == date(2026, 11, 27)  # Sunday → Friday


def test_window_starts_at_midnight_et_of_the_previous_weekday_without_a_database():
    # 02:30 UTC on Tuesday is still Monday evening in New York
    now = datetime.fromisoformat("2026-12-01T02:30:00+00:00")
    assert window_start(None, now) == datetime(2026, 11, 27, tzinfo=EASTERN)
    now = datetime.fromisoformat("2026-12-01T15:00:00+00:00")
    assert window_start(None, now) == datetime(2026, 11, 30, tzinfo=EASTERN)
