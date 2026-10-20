"""UI routes with fake layers: expected filings, watchlist, recap, peers, CSV exports."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer, FakeStore

TODAY = date(2026, 8, 15)
EXPECTED = {"company_id": NVDA, "form": "10-Q", "period_end": date(2026, 10, 25),
            "expected_date": date(2026, 11, 19), "deadline": date(2026, 12, 4),
            "deadline_days": 40, "filer_category": "large_accelerated", "basis": "history",
            "typical_lag_days": 25, "lag_samples": 8, "last_period_end": date(2026, 7, 26),
            "last_filed": date(2026, 8, 26), "status": "upcoming"}


class UIStore(FakeStore):
    def expected_filings(self, user_id, company_ids, as_of, periods):
        self.calls.append(("expected_filings", user_id, tuple(company_ids), as_of, periods))
        return [{**EXPECTED, "company_id": c} for c in company_ids]


@pytest.fixture
def client():
    store = UIStore()
    c = TestClient(create_app(FakeAuthorizer({"alice": {NVDA: {"financials"}}}), store, SECRET,
                              today=lambda: TODAY))
    c.store = store
    return c


def test_expected_filings_are_public_estimates(client, token):
    """Derived from the public filing index only: no tier needed, still through Store."""
    r = client.get(f"/v1/companies/{TSLA}/expected-filings?periods=1", headers=token("carol"))
    assert r.status_code == 200
    body = r.json()
    assert body["as_of"] == "2026-08-15" and "Estimate" in body["method"]
    assert body["expected"][0] | {"estimate": True} == body["expected"][0]
    assert body["expected"][0]["expected_date"] == "2026-11-19"
    assert client.store.calls == [("expected_filings", "carol", (TSLA,), TODAY, 1)]


def test_expected_filings_need_a_token(client):
    assert client.get(f"/v1/companies/{NVDA}/expected-filings").status_code == 401
