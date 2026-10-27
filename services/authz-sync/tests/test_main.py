from prometheus_client import REGISTRY

from authz_sync.main import next_batch, reconcile
from authz_sync.state import AppState


class FakeReconciler:
    def apply(self, tuples, access):
        return {"tuples_added": 2, "tuples_deleted": 1, "access_added": 0, "access_deleted": 0}


def sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0


def test_reconcile_counts_changes_and_propagation():
    added = sample("tributary_authz_changes_total", {"kind": "tuples_added"})
    runs = sample("tributary_authz_reconciles_total")
    observed = sample("tributary_authz_propagation_seconds_count")
    total = sample("tributary_authz_propagation_seconds_sum")
    reconcile(AppState(), FakeReconciler(), "1 events", since_commit_ms=1_800_000_000_000,
              clock=lambda: 1_800_000_000.5)
    assert sample("tributary_authz_changes_total", {"kind": "tuples_added"}) == added + 2
    assert sample("tributary_authz_reconciles_total") == runs + 1
    assert sample("tributary_authz_propagation_seconds_count") == observed + 1
    assert sample("tributary_authz_propagation_seconds_sum") - total == 0.5


def test_startup_reconcile_records_no_propagation():
    observed = sample("tributary_authz_propagation_seconds_count")
    reconcile(AppState(), FakeReconciler(), "startup replay")
    assert sample("tributary_authz_propagation_seconds_count") == observed


class FakeConsumer:
    def __init__(self, ready):
        self.ready, self.calls = list(ready), []

    def poll(self, timeout):
        self.calls.append(("poll", timeout))
        return self.ready.pop(0) if self.ready else None

    def consume(self, num_messages, timeout):
        self.calls.append(("consume", num_messages, timeout))
        taken, self.ready = self.ready[:num_messages], self.ready[num_messages:]
        return taken


def test_next_batch_returns_once_the_first_message_is_in():
    consumer = FakeConsumer(["a", "b", "c"])
    assert next_batch(consumer, timeout=0.5, linger=0.05, limit=500) == ["a", "b", "c"]
    # only the short linger after the first message, never a second full timeout
    assert consumer.calls == [("poll", 0.5), ("consume", 499, 0.05)]


def test_next_batch_is_empty_when_idle():
    consumer = FakeConsumer([])
    assert next_batch(consumer, timeout=0.5) == []
    assert consumer.calls == [("poll", 0.5)]


def test_next_batch_respects_the_batch_size():
    consumer = FakeConsumer(list(range(10)))
    assert next_batch(consumer, limit=4) == [0, 1, 2, 3]
