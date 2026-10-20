"""UI routes with fake layers: expected filings, watchlist, recap, peers, CSV exports."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer, FakeStore
from .test_screener import ScreenStore

TODAY = date(2026, 8, 15)
EXPECTED = {"company_id": NVDA, "form": "10-Q", "period_end": date(2026, 10, 25),
            "expected_date": date(2026, 11, 19), "deadline": date(2026, 12, 4),
            "deadline_days": 40, "filer_category": "large_accelerated", "basis": "history",
            "typical_lag_days": 25, "lag_samples": 8, "last_period_end": date(2026, 7, 26),
            "last_filed": date(2026, 8, 26), "status": "upcoming"}


class FakeWatchlists:
    def __init__(self, entries):
        self.entries = dict(entries)  # user → [company_id]

    def list(self, user_id):
        return [{"company_id": c, "added_at": "2026-10-01T00:00:00Z"}
                for c in self.entries.get(user_id, [])]

    def count(self, user_id):
        return len(self.entries.get(user_id, []))

    def add(self, user_id, company_id):
        mine = self.entries.setdefault(user_id, [])
        if company_id in mine:
            return False
        mine.append(company_id)
        return True

    def remove(self, user_id, company_id):
        mine = self.entries.get(user_id, [])
        if company_id not in mine:
            return False
        mine.remove(company_id)
        return True


class UIStore(ScreenStore, FakeStore):
    def __init__(self):
        ScreenStore.__init__(self)
        FakeStore.__init__(self)

    def last_reports(self, user_id, company_ids):
        return {c: {"accession_no": "x", "form": "10-Q", "filing_date": date(2026, 8, 26),
                    "period_end": date(2026, 7, 26)} for c in company_ids}

    def expected_filings(self, user_id, company_ids, as_of, periods):
        self.calls.append(("expected_filings", user_id, tuple(company_ids), as_of, periods))
        return [{**EXPECTED, "company_id": c} for c in company_ids]


@pytest.fixture
def client():
    store, lists = UIStore(), FakeWatchlists({"alice": [TSLA, NVDA]})
    c = TestClient(create_app(FakeAuthorizer({"alice": {NVDA: {"financials"}}}), store, SECRET,
                              today=lambda: TODAY, watchlists=lists))
    c.store, c.lists = store, lists
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


def test_watchlist_never_grants_access(client, token):
    """alice watches TSLA without a grant: metadata, last filing and next expected filing, but
    no values; the query only reads values of NVDA."""
    r = client.get("/v1/watchlist", headers=token("alice"))
    assert r.status_code == 200
    rows = {c["company_id"]: c for c in r.json()["companies"]}
    assert rows[TSLA]["values"] == {k: None for k in rows[TSLA]["values"]}
    assert rows[TSLA]["access"] == [] and rows[TSLA]["name"] == "Tesla, Inc."
    assert rows[TSLA]["last_filing"]["form"] == "10-Q"
    assert rows[TSLA]["next_expected"]["expected_date"] == "2026-11-19"
    assert rows[NVDA]["values"]["revenue_ttm"] == "130497000000"
    assert list(rows[NVDA]["values"]) == ["revenue_ttm", "revenue_growth_q",
                                          "operating_margin_ttm", "free_cash_flow_ttm"]
    compiled = client.store.compiled[0]
    assert compiled.params["granted"] == [NVDA]
    assert compiled.params["ids"] == sorted([TSLA, NVDA])


def test_watchlist_is_per_user(client, token):
    assert client.get("/v1/watchlist", headers=token("bob")).json()["companies"] == []
    assert client.store.compiled[0].params["ids"] == []


def test_watchlist_csv(client, token):
    r = client.get("/v1/watchlist?format=csv", headers=token("alice"))
    assert r.status_code == 200
    header, *lines = r.text.splitlines()
    assert header.split(",")[-4:] == ["revenue_ttm", "revenue_growth_q", "operating_margin_ttm",
                                      "free_cash_flow_ttm"]
    tsla = next(line for line in lines if line.startswith(TSLA))
    assert tsla.endswith(",,,,") and "2026-11-19" in tsla


def test_watch_and_unwatch(client, token):
    class Profiles(UIStore):
        def profiles(self, user_id, ids):
            return {i: {"name": "x"} for i in ids if i != "0000000000"}

    lists = FakeWatchlists({})
    c = TestClient(create_app(FakeAuthorizer({}), Profiles(), SECRET, watchlists=lists))
    assert c.put(f"/v1/watchlist/{TSLA}", headers=token("carol")).json() == \
        {"company_id": TSLA, "added": True}
    assert c.put(f"/v1/watchlist/{TSLA}", headers=token("carol")).json()["added"] is False
    assert lists.entries == {"carol": [TSLA]}
    assert c.put("/v1/watchlist/0000000000", headers=token("carol")).status_code == 404
    assert c.delete(f"/v1/watchlist/{TSLA}", headers=token("carol")).json()["removed"] is True
    assert lists.entries == {"carol": []}


def test_watchlist_without_app_database_is_503(token):
    c = TestClient(create_app(FakeAuthorizer({}), UIStore(), SECRET))
    assert c.get("/v1/watchlist", headers=token("alice")).status_code == 503
    assert c.put(f"/v1/watchlist/{TSLA}", headers=token("alice")).status_code == 503


def test_calendar_over_the_watchlist(client, token):
    r = client.get("/v1/calendar/expected-filings?days=120", headers=token("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["through"] == "2026-12-13"
    assert {f["company_id"] for f in body["filings"]} == {NVDA, TSLA}
    assert all(f["expected_date"] == "2026-11-19" and f["estimate"] for f in body["filings"])
    assert ("expected_filings", "alice", (NVDA, TSLA), TODAY, 2) in client.store.calls


def test_calendar_window(client, token):
    body = client.get("/v1/calendar/expected-filings?days=30", headers=token("alice")).json()
    assert body["filings"] == []  # expected 2026-11-19 is after 2026-09-14


def test_calendar_of_readable_companies(client, token):
    client.get("/v1/calendar/expected-filings?scope=readable", headers=token("alice"))
    assert ("expected_filings", "alice", (NVDA,), TODAY, 2) in client.store.calls
