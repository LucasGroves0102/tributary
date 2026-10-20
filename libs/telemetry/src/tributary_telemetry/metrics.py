"""Kafka message counters, and the /metrics server for services without an HTTP app."""

import logging
import os
from typing import Protocol

from prometheus_client import Counter, start_http_server

log = logging.getLogger(__name__)

CONSUMED = Counter("tributary_messages_consumed_total", "Kafka messages consumed", ["topic"])
PRODUCED = Counter("tributary_messages_produced_total", "Kafka messages delivered", ["topic"])
SKIPPED = Counter("tributary_messages_skipped_total",
                  "Consumed messages dropped without being processed", ["topic", "reason"])
DELIVERY_FAILED = Counter("tributary_messages_delivery_failed_total",
                          "Produced messages Kafka did not accept", ["topic"])


class _Message(Protocol):
    def topic(self) -> str | None: ...


def count_delivery(err: object, msg: _Message) -> None:
    """Producer on_delivery callback: count the message as delivered or failed."""
    topic = msg.topic() or ""
    if err is None:
        PRODUCED.labels(topic).inc()
    else:
        DELIVERY_FAILED.labels(topic).inc()
        log.error("delivery to %s failed: %s", topic, err)


def serve_metrics(port: str | None = None) -> None:
    """Serve /metrics on `port` or $METRICS_PORT from a background thread; no-op if neither."""
    port = port or os.environ.get("METRICS_PORT")
    if port:
        start_http_server(int(port))
        log.info("serving metrics on port %s", port)
