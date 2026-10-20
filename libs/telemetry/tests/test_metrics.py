from types import SimpleNamespace as NS

from prometheus_client import REGISTRY

from tributary_telemetry import count_delivery


def sample(name, topic):
    return REGISTRY.get_sample_value(name, {"topic": topic}) or 0


def test_count_delivery_counts_delivered_and_failed_messages():
    msg = NS(topic=lambda: "test.topic")
    delivered = sample("tributary_messages_produced_total", "test.topic")
    failed = sample("tributary_messages_delivery_failed_total", "test.topic")
    count_delivery(None, msg)
    count_delivery("broker down", msg)
    assert sample("tributary_messages_produced_total", "test.topic") == delivered + 1
    assert sample("tributary_messages_delivery_failed_total", "test.topic") == failed + 1
