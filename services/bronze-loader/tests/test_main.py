import json
from datetime import datetime, timedelta

import pytest
from confluent_kafka import KafkaError, KafkaException
from prometheus_client import REGISTRY

from bronze_loader.main import count, readable, to_rows
from bronze_loader.rows import to_row


class Msg:
    def __init__(self, error=None, value=b"", offset=0):
        self._error = KafkaError(error) if error is not None else None
        self._value, self._offset = value, offset

    def error(self):
        return self._error

    def value(self):
        return self._value

    def partition(self):
        return 0

    def offset(self):
        return self._offset


def sample(name, **labels):
    return REGISTRY.get_sample_value(name, {"topic": "filings.raw", **labels}) or 0


def test_to_rows_counts_consumed_and_malformed_messages():
    event = {"accession_no": "0001213900-26-110436", "cik": "0002042022", "form_type": "4",
             "filed_at": "2026-10-16T21:42:41-04:00"}
    consumed = sample("tributary_messages_consumed_total")
    malformed = sample("tributary_messages_skipped_total", reason="malformed")
    rows = to_rows([Msg(value=json.dumps(event).encode(), offset=1),
                    Msg(value=b"not json", offset=2)])
    assert [r.kafka_offset for r in rows] == [1]
    assert sample("tributary_messages_consumed_total") == consumed + 2
    assert sample("tributary_messages_skipped_total", reason="malformed") == malformed + 1


def test_readable_keeps_data_messages():
    ok = Msg()
    assert readable([ok]) == [ok]


def test_readable_skips_missing_topic():
    ok = Msg()
    assert readable([Msg(KafkaError.UNKNOWN_TOPIC_OR_PART), ok]) == [ok]
    assert readable([Msg(KafkaError.UNKNOWN_TOPIC_OR_PART)]) == []


def test_readable_raises_other_errors():
    with pytest.raises(KafkaException):
        readable([Msg(KafkaError._TRANSPORT)])


ACCEPTED = datetime.fromisoformat("2026-10-16T21:42:41-04:00")


def row(acc, cik, source=None, offset=0):
    e = {"accession_no": acc, "cik": cik, "form_type": "4", "filed_at": ACCEPTED.isoformat()}
    if source:
        e["source"] = source
    return to_row(json.dumps(e).encode(), 0, offset)


def filings(result, source):
    return REGISTRY.get_sample_value("tributary_bronze_filings_total",
                                     {"result": result, "source": source}) or 0


def latency(source, suffix="count", le=None):
    labels = {"source": source, **({"le": le} if le else {})}
    return REGISTRY.get_sample_value(f"tributary_bronze_filing_latency_seconds_{suffix}",
                                     labels) or 0


def test_count_splits_by_source_and_times_each_new_filing_once():
    keys = [("inserted", "feed"), ("duplicate", "feed"), ("inserted", "recheck"),
            ("inserted", "daily-index"), ("inserted", "other")]
    before = {k: filings(*k) for k in keys}
    feed_n, recheck_n = latency("feed"), latency("recheck")
    under_a_minute = latency("feed", "bucket", "60.0")
    form4 = [row("a", "1", "feed"), row("a", "2")]  # one filing, two filers; no source = feed
    late = row("b", "3", "recheck")
    nightly = row("c", "4", "daily-index")
    again = row("d", "5", "feed")  # already in bronze
    odd = row("e", "6", "something-new")
    rows = [*form4, late, nightly, again, odd]
    inserted = [(form4[0], ACCEPTED + timedelta(seconds=40)),
                (form4[1], ACCEPTED + timedelta(seconds=40)),
                (late, ACCEPTED + timedelta(minutes=50)),
                (nightly, ACCEPTED + timedelta(hours=9)),
                (odd, ACCEPTED)]
    assert count(rows, inserted) == 5
    assert filings("inserted", "feed") == before[("inserted", "feed")] + 2
    assert filings("duplicate", "feed") == before[("duplicate", "feed")] + 1
    assert filings("inserted", "recheck") == before[("inserted", "recheck")] + 1
    assert filings("inserted", "daily-index") == before[("inserted", "daily-index")] + 1
    assert filings("inserted", "other") == before[("inserted", "other")] + 1
    # latency: the Form 4 once, the recheck's on its own series, the daily index's not at all
    # (its time is midnight ET, not the acceptance)
    assert latency("feed") == feed_n + 1
    assert latency("feed", "bucket", "60.0") == under_a_minute + 1
    assert latency("recheck") == recheck_n + 1
    assert REGISTRY.get_sample_value("tributary_bronze_filing_latency_seconds_count",
                                     {"source": "daily-index"}) is None


def test_every_series_exists_before_its_first_filing():
    # increase() over a window misses a counter's first value when the series appears with it
    for source in ("feed", "recheck", "daily-index", "seed", "other"):
        assert REGISTRY.get_sample_value("tributary_bronze_filings_total",
                                         {"result": "inserted", "source": source}) is not None
