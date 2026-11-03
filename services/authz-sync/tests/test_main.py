from prometheus_client import REGISTRY

from authz_sync.main import Catalog, assign_new_topics, next_batch, reconcile
from authz_sync.state import AppState


class FakeReconciler:
    def apply(self, desired):
        return {"tuples_added": 2, "tuples_deleted": 1, "access_added": 0, "access_deleted": 0}


def sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0


def test_reconcile_counts_changes_and_propagation():
    added = sample("tributary_authz_changes_total", {"kind": "tuples_added"})
    runs = sample("tributary_authz_reconciles_total")
    observed = sample("tributary_authz_propagation_seconds_count")
    total = sample("tributary_authz_propagation_seconds_sum")
    reconcile(AppState(), FakeReconciler(), frozenset(), "1 events",
              since_commit_ms=1_800_000_000_000,
              clock=lambda: 1_800_000_000.5)
    assert sample("tributary_authz_changes_total", {"kind": "tuples_added"}) == added + 2
    assert sample("tributary_authz_reconciles_total") == runs + 1
    assert sample("tributary_authz_propagation_seconds_count") == observed + 1
    assert sample("tributary_authz_propagation_seconds_sum") - total == 0.5


def test_startup_reconcile_records_no_propagation():
    observed = sample("tributary_authz_propagation_seconds_count")
    reconcile(AppState(), FakeReconciler(), frozenset(), "startup replay")
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


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_catalog_refreshes_on_its_interval_and_reports_changes():
    loads = [{"a", "b"}, {"a", "b"}, {"a", "b", "c"}]
    clock = Clock()
    cat = Catalog(lambda: loads.pop(0), every=600, clock=clock)
    assert cat.due()
    assert cat.refresh() is True and cat.companies == {"a", "b"}
    assert not cat.due()
    clock.t += 600
    assert cat.due() and cat.refresh() is False  # same set: no reconcile needed
    clock.t += 600
    assert cat.refresh() is True and cat.companies == {"a", "b", "c"}


def test_catalog_keeps_its_set_when_a_load_fails_or_collapses():
    import psycopg

    def broken():
        raise psycopg.OperationalError("connection lost")

    cat = Catalog(lambda: {"a", "b", "c", "d"}, every=600, clock=Clock())
    cat.refresh()
    refused = sample("tributary_authz_catalog_refused_total")
    cat._load = broken
    assert cat.refresh() is False and cat.companies == {"a", "b", "c", "d"}
    cat._load = lambda: {"a"}  # a gold rebuild half-way, say
    assert cat.refresh() is False and cat.companies == {"a", "b", "c", "d"}
    assert sample("tributary_authz_catalog_refused_total") == refused + 1


class Topics:
    def __init__(self, names):
        self.topics = {n: type("T", (), {"partitions": {0: None}})() for n in names}


class TopicConsumer:
    def __init__(self, names):
        self.names, self.assigned = names, []

    def list_topics(self, timeout):
        return Topics(self.names)

    def incremental_assign(self, partitions):
        self.assigned += [(tp.topic, tp.partition, tp.offset) for tp in partitions]


def test_an_optional_topic_is_read_from_the_beginning_once_it_appears():
    from confluent_kafka import OFFSET_BEGINNING
    consumer = TopicConsumer(["app.public.users"])
    assert assign_new_topics(consumer, ["app.public.public_catalogs"]) == [
        "app.public.public_catalogs"]
    assert consumer.assigned == []
    consumer.names.append("app.public.public_catalogs")
    assert assign_new_topics(consumer, ["app.public.public_catalogs"]) == []
    assert consumer.assigned == [("app.public.public_catalogs", 0, OFFSET_BEGINNING)]
