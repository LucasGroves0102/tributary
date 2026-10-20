"""UI routes with fake layers: expected filings, watchlist, recap, peers, CSV exports."""

from datetime import date
from decimal import Decimal

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


ACC_NVDA, ACC_TSLA, ACC_8K = "0001045810-26-000075", "0001318605-26-000010", "0001045810-26-000080"


def recap_row(comparison, metric, value, period_end, unit="USD"):
    return {"comparison": comparison, "metric": metric, "span": "quarter",
            "period_start": None, "period_end": period_end, "fiscal_year": 2027,
            "fiscal_quarter": 2, "calendar_year": 2026, "calendar_quarter": 2,
            "value": Decimal(value), "unit": unit, "value_source": "reported",
            "source_tag": "us-gaap:Revenues", "accession_no": ACC_NVDA, "form": "10-Q",
            "filed": date(2026, 8, 26), "source_accessions": [ACC_NVDA],
            "ingested_at": "2026-08-26T21:00:00Z"}


class RecapStore(UIStore):
    def filing(self, user_id, accession_no):
        self.calls.append(("filing", accession_no))
        return {ACC_NVDA: {"company_id": NVDA, "cik": "0001045810", "form": "10-Q",
                           "filing_date": date(2026, 8, 26), "period_end": date(2026, 7, 26)},
                ACC_TSLA: {"company_id": TSLA, "cik": "0001318605", "form": "10-Q",
                           "filing_date": date(2026, 7, 23), "period_end": date(2026, 6, 30)},
                ACC_8K: {"company_id": NVDA, "cik": "0001045810", "form": "8-K",
                         "filing_date": date(2026, 8, 26), "period_end": None},
                }.get(accession_no)

    def recap(self, user_id, company_id, accession_no, period_end, metrics):
        self.calls.append(("recap", company_id, period_end, tuple(metrics)))
        return [recap_row("current", "revenue", "46743000000", date(2026, 7, 26)),
                recap_row("prior_quarter", "revenue", "44062000000", date(2026, 4, 26)),
                recap_row("year_ago", "revenue", "30040000000", date(2025, 7, 27)),
                recap_row("current", "gross_margin", "0.72", date(2026, 7, 26), "ratio"),
                recap_row("year_ago", "gross_margin", "0.75", date(2025, 7, 27), "ratio"),
                recap_row("current", "net_income", "5", date(2026, 7, 26)),
                recap_row("prior_quarter", "net_income", "0", date(2026, 4, 26)),
                recap_row("year_ago", "cash", "1", date(2025, 7, 27))]  # no current: dropped

    def filing_flags(self, user_id, accession_no):
        self.calls.append(("filing_flags", accession_no))
        return [{"flag": "impairment", "section_key": "I-2", "title": "MD&A", "matches": 2,
                 "char_start": 51234, "snippet": "...goodwill impairment of...",
                 "document_url": "https://www.sec.gov/x.htm", "form": "10-Q",
                 "filed": date(2026, 8, 26)}]


def recap_client(grants):
    store = RecapStore()
    c = TestClient(create_app(FakeAuthorizer(grants), store, SECRET))
    c.store = store
    return c


def test_recap_changes_and_citations(token):
    c = recap_client({"alice": {NVDA: {"financials"}}})
    r = c.get(f"/v1/filings/{ACC_NVDA}/recap", headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    m = {(e["metric"], e["span"]): e for e in body["metrics"]}
    rev = m[("revenue", "quarter")]
    assert rev["current"]["value"] == "46743000000"
    assert rev["current"]["fiscal_period"] == "FY2027 Q2"
    assert rev["current"]["citation"]["filing_url"] == \
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"
    assert (rev["prior_quarter"]["change"], rev["prior_quarter"]["pct_change"]) == \
        ("2681000000", "0.060846")
    assert (rev["year_ago"]["change"], rev["year_ago"]["pct_change"]) == \
        ("16703000000", "0.556025")
    gm = m[("gross_margin", "quarter")]
    assert (gm["year_ago"]["change"], gm["year_ago"]["pct_change"]) == ("-0.03", None)
    assert gm["prior_quarter"] is None
    assert m[("net_income", "quarter")]["prior_quarter"]["pct_change"] is None  # from 0
    assert ("cash", "quarter") not in m
    # free plan: no filings tier, so no flags and no section query
    assert body["flags"] is None and "flags_unavailable" in body
    assert ("filing_flags", ACC_NVDA) not in c.store.calls
    assert c.store.calls[1][:3] == ("recap", NVDA, date(2026, 7, 26))


def test_recap_flags_with_filings_access(token):
    c = recap_client({"bob": {NVDA: {"financials", "filings"}}})
    body = c.get(f"/v1/filings/{ACC_NVDA}/recap", headers=token("bob")).json()
    assert body["flags"][0]["citation"] == {
        "accession_no": ACC_NVDA, "form": "10-Q", "filed": "2026-08-26", "section_key": "I-2",
        "char_start": 51234, "document_url": "https://www.sec.gov/x.htm"}
    assert "flags_unavailable" not in body


def test_recap_denied_and_unknown_look_the_same(token):
    c = recap_client({"alice": {NVDA: {"financials"}}})
    denied = c.get(f"/v1/filings/{ACC_TSLA}/recap", headers=token("alice"))
    unknown = c.get("/v1/filings/0000000000-26-000001/recap", headers=token("alice"))
    assert denied.status_code == unknown.status_code == 403
    assert denied.json() == unknown.json()
    assert not any(call[0] in ("recap", "filing_flags") for call in c.store.calls)


def test_recap_only_for_periodic_reports(token):
    c = recap_client({"alice": {NVDA: {"financials"}}})
    assert c.get(f"/v1/filings/{ACC_8K}/recap", headers=token("alice")).status_code == 422


@pytest.mark.parametrize("path", ["/v1/filings/0001045810-26-75/recap",
                                  f"/v1/filings/{ACC_NVDA}/recap?metrics=salary"])
def test_recap_rejects_bad_input(token, path):
    c = recap_client({"alice": {NVDA: {"financials"}}})
    assert c.get(path, headers=token("alice")).status_code == 422
    assert c.store.calls == []


AAPL = "0000320193"


class PeerStore(UIStore):
    def peers(self, user_id, company_id, granted, revenue, limit):
        self.calls.append(("peers", company_id, tuple(granted), revenue, limit))
        return [{"company_id": NVDA, "sic_level": 4, "same_filer_category": True,
                 "revenue_band": 4 if NVDA in granted else None},
                {"company_id": TSLA, "sic_level": 2, "same_filer_category": True,
                 "revenue_band": None}]


def peer_client(grants):
    store = PeerStore()
    c = TestClient(create_app(FakeAuthorizer(grants), store, SECRET))
    c.store = store
    return c


def test_peers_metrics_only_where_permitted(token):
    c = peer_client({"alice": {NVDA: {"financials"}}})
    r = c.get(f"/v1/companies/{NVDA}/peers?limit=5", headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["company"]["revenue_band"] == ">=$100B"
    peers = {p["company_id"]: p for p in body["peers"]}
    assert peers[NVDA]["values"]["revenue_ttm"] == "130497000000"
    assert peers[NVDA]["match"] == {"sic_level": 4, "same_filer_category": True,
                                    "revenue_band": ">=$100B", "same_revenue_band": True}
    assert set(peers[TSLA]["values"].values()) == {None}
    assert peers[TSLA]["match"]["revenue_band"] is None
    assert ("peers", NVDA, (NVDA,), Decimal("130497000000"), 5) in c.store.calls


def test_peers_of_an_ungranted_company_ignore_its_revenue(token):
    c = peer_client({"carol": {}})
    body = c.get(f"/v1/companies/{TSLA}/peers", headers=token("carol")).json()
    assert body["company"]["revenue_band"] is None
    assert ("peers", TSLA, (), None, 10) in c.store.calls
    assert all(v is None for p in body["peers"] for v in p["values"].values())


def test_peers_of_unknown_company(token):
    c = peer_client({})
    assert c.get(f"/v1/companies/{AAPL}/peers", headers=token("carol")).status_code == 404
