import pytest
from confluent_kafka import KafkaError, KafkaException

from bronze_loader.main import readable


class Msg:
    def __init__(self, error=None):
        self._error = KafkaError(error) if error is not None else None

    def error(self):
        return self._error


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
