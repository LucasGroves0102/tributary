"""Publish filings to Kafka."""

import json
import logging

from confluent_kafka import KafkaError, Message, Producer

from poller.edgar import Filing

TOPIC = "filings.raw"

log = logging.getLogger(__name__)


class FilingProducer:
    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "enable.idempotence": True,  # no broker-side duplicates on retry
                "acks": "all",
            }
        )

    def publish(self, filing: Filing) -> None:
        """Send one filing to TOPIC, keyed by CIK so a company's events stay ordered."""
        self._producer.produce(
            TOPIC,
            key=filing.cik.encode(),
            value=json.dumps(filing.to_dict()).encode(),
            on_delivery=_on_delivery,
        )
        self._producer.poll(0)  # serve delivery callbacks

    def flush(self, timeout: float = 10.0) -> None:
        remaining = self._producer.flush(timeout)
        if remaining:
            log.warning("%d messages still undelivered after flush", remaining)


def _on_delivery(err: KafkaError | None, msg: Message) -> None:
    if err is not None:
        log.error("delivery failed for key=%s: %s", msg.key(), err)
