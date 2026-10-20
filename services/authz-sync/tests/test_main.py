from prometheus_client import REGISTRY

from authz_sync.main import reconcile
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
