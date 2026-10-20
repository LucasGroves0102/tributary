import json

import pytest
from confluent_kafka import KafkaError, KafkaException
from prometheus_client import REGISTRY

from bronze_loader.main import readable, to_rows


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
