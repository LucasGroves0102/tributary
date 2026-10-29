"""Route behavior with fake layers: who gets checked, what's refused, and failing closed."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import AuthzUnavailable

from .conftest import SECRET

NVDA, TSLA = "0001045810", "0001318605"


class FakeAuthorizer:
    def __init__(self, grants: dict[str, dict[str, set[str]]], down: bool = False):
        self.grants, self.down, self.checks = grants, down, []

    def can(self, user_id, tier, company_id):
        self.checks.append((user_id, tier, company_id))
        if self.down:
            raise AuthzUnavailable("down")
        return tier in self.grants.get(user_id, {}).get(company_id, set())

    def readable(self, user_id):
        if self.down:
            raise AuthzUnavailable("down")
        return self.grants.get(user_id, {})


class FakeStore:
    def __init__(self):
        self.calls = []
        self.metric_reads = 0

    def metric_names(self):
        self.metric_reads += 1
        return {"metrics": ["eps_diluted", "revenue"], "derived_metrics": ["gross_margin", "roe"]}

    def financials(self, user_id, company_id, metrics, quarters, view, basis):
        self.calls.append(("financials", user_id, company_id))
        self.financials_args = (metrics, quarters, view, basis)
        return [{"metric": "revenue", "period_start": date(2026, 4, 27),
                 "period_end": date(2026, 7, 26), "fiscal_year": 2027, "fiscal_quarter": 2,
                 "calendar_year": 2026, "calendar_quarter": 2,
                 "value": 96221000000, "unit": "USD",
                 "value_source": "reported", "source_tag": "us-gaap:Revenues",
                 "accession_no": "0001045810-26-000075", "form": "10-Q",
                 "filed": date(2026, 8, 26), "source_accessions": ["0001045810-26-000075"],
                 "ingested_at": "2026-10-17T09:56:49Z"}]

    def segment_metric_names(self):
        return ["operating_income", "revenue"]

    def feed_status(self):
        return {"last_accepted_at": "2026-10-28T21:12:49Z",
                "last_ingested_at": "2026-10-28T21:13:20Z"}

    def segments(self, user_id, company_id, axis, metric, periods, view, basis):
        self.calls.append(("segments", user_id, company_id))
        self.segments_args = (axis, metric, periods, view, basis)
        return [{"axis": "product_service", "member": "nvda:DataCenterMember",
                 "member_label": "Data Center", "member_aliases": [], "metric": "revenue",
                 "period_start": date(2026, 4, 27), "period_end": date(2026, 7, 26),
                 "fiscal_year": 2027, "fiscal_quarter": 2, "calendar_year": 2026,
                 "calendar_quarter": 2, "value": Decimal("89023000000.10"), "unit": "USD",
                 "value_source": "reported", "source_tag": "us-gaap:Revenues",
                 "axis_qname": "srt:ProductOrServiceAxis",
                 "accession_no": "0001045810-26-000075", "filer_cik": "0001045810",
                 "form": "10-Q", "filed": date(2026, 8, 26),
                 "source_accessions": ["0001045810-26-000075"],
                 "ingested_at": "2026-10-17T09:56:49Z"}]

    def sections(self, *args):
        self.calls.append(("sections",) + args[:2])
        return []

    def filings(self, *args):
        self.calls.append(("filings",) + args[:2])
        self.filings_args = args[2:]
        return []

    def latest_filings(self, user_id, company_ids, limit):
        self.calls.append(("latest_filings", user_id, tuple(company_ids)))
        return []

    def search(self, user_id, q, only=None):
        self.calls.append(("search", q, None if only is None else tuple(only)))
        return [{"company_id": NVDA, "name": "NVIDIA CORP", "matched_on": "ticker",
                 "matched_alias": "NVDA", "is_current": True, "sic": "3674",
                 "industry": "Semiconductors & Related Devices", "exchanges": ["Nasdaq"]},
                {"company_id": TSLA, "name": "Tesla, Inc.", "matched_on": "name",
                 "matched_alias": "Tesla, Inc.", "is_current": True, "sic": None,
                 "industry": None, "exchanges": None}]

    def profiles(self, user_id, ids):
        return {NVDA: {"name": "NVIDIA CORP", "sic": "3674",
                       "industry": "Semiconductors & Related Devices", "exchanges": ["Nasdaq"]}}


GRANTS = {"alice": {NVDA: {"financials"}}}


@pytest.fixture
def client():
    store, authz = FakeStore(), FakeAuthorizer(GRANTS)
    c = TestClient(create_app(authz, store, SECRET))
    c.store, c.authz = store, authz
    return c


def test_requires_token(client):
    assert client.get(f"/v1/companies/{NVDA}/financials").status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert client.get(f"/v1/companies/{NVDA}/financials", headers=bad).status_code == 401


def test_permitted_financials_with_citation(client, token):
    r = client.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["citation"]["accession_no"] == "0001045810-26-000075"
    assert row["freshness"]["filed"] == "2026-08-26"
    assert "as_of" in r.json()


def test_denied_never_reaches_the_database(client, token):
    for path in (f"/v1/companies/{TSLA}/financials", f"/v1/companies/{NVDA}/filings",
                 f"/v1/companies/{NVDA}/sections/1A"):
        assert client.get(path, headers=token("alice")).status_code == 403
    assert client.store.calls == []


def test_filings_of_one_accession_for_the_reader(token):
    store = FakeStore()
    c = TestClient(create_app(FakeAuthorizer({"bob": {NVDA: {"filings"}}}), store, SECRET))
    r = c.get(f"/v1/companies/{NVDA}/filings?accession_no=0001045810-24-000001",
              headers=token("bob"))
    assert r.status_code == 200
    assert store.filings_args == (8, "0001045810-24-000001")
    assert c.get(f"/v1/companies/{NVDA}/filings?accession_no=x",
                 headers=token("bob")).status_code == 422


def test_user_comes_from_token_not_parameters(client, token):
    client.get(f"/v1/companies/{NVDA}/financials?user_id=bob", headers=token("alice"))
    assert client.authz.checks == [("alice", "financials", NVDA)]


def test_fails_closed_when_openfga_is_down(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), FakeStore(), SECRET))
    assert c.get(f"/v1/companies/{NVDA}/financials", headers=token("alice")).status_code == 503
    assert c.get("/v1/me", headers=token("alice")).status_code == 503


@pytest.mark.parametrize("path", [
    "/v1/companies/NVDA/financials",              # not a company_id
    "/v1/companies/0001045810'--/financials",     # injection attempt
    f"/v1/companies/{NVDA}/sections/1A;DROP",     # bad section key
])
def test_rejects_malformed_identifiers(client, token, path):
    assert client.get(path, headers=token("alice")).status_code in (404, 422)
    assert client.store.calls == []


def test_metric_list_is_read_from_the_store_once(client, token):
    for _ in range(2):
        assert client.get("/v1/metrics").json() == {
            "metrics": ["eps_diluted", "revenue"], "derived_metrics": ["gross_margin", "roe"]}
    client.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    assert client.store.metric_reads == 1


def test_unknown_metric(client, token):
    r = client.get(f"/v1/companies/{NVDA}/financials?metrics=salary", headers=token("alice"))
    assert r.status_code == 422


def test_financials_views_and_bases(client, token):
    r = client.get(f"/v1/companies/{NVDA}/financials?view=ttm&basis=calendar"
                   "&metrics=revenue&metrics=gross_margin&quarters=8", headers=token("alice"))
    assert r.status_code == 200
    assert (r.json()["view"], r.json()["basis"]) == ("ttm", "calendar")
    assert client.store.financials_args == (["revenue", "gross_margin"], 8, "ttm", "calendar")
    row = r.json()["rows"][0]
    assert (row["fiscal_period"], row["calendar_period"]) == ("FY2027 Q2", "CY2026 Q2")


def test_financials_default_to_fiscal_quarters_of_every_metric(client, token):
    client.get(f"/v1/companies/{NVDA}/financials", headers=token("alice"))
    metrics, quarters, view, basis = client.store.financials_args
    assert (quarters, view, basis) == (4, "quarter", "fiscal")
    assert "revenue" in metrics and "roe" in metrics


@pytest.mark.parametrize("query", ["view=annual", "basis=lunar"])
def test_financials_reject_unknown_view_or_basis(client, token, query):
    r = client.get(f"/v1/companies/{NVDA}/financials?{query}", headers=token("alice"))
    assert r.status_code == 422 and client.store.calls == []


def test_derived_metrics_are_still_permission_checked(client, token):
    r = client.get(f"/v1/companies/{TSLA}/financials?metrics=roe&view=ttm",
                   headers=token("alice"))
    assert r.status_code == 403 and client.store.calls == []


def test_search_shows_access_per_result(client, token):
    results = client.get("/v1/companies/search?q=n", headers=token("alice")).json()["results"]
    assert {r["company_id"]: r["access"] for r in results} == {NVDA: ["financials"], TSLA: []}


def test_search_can_list_only_readable_companies(client, token):
    """A demo sandbox's pickers ask for its companies only; the filter is the caller's grants."""
    r = client.get("/v1/companies/search?q=n&access=financials", headers=token("alice"))
    assert r.status_code == 200, r.text
    assert ("search", "n", (NVDA,)) in client.store.calls
    client.get("/v1/companies/search?q=n", headers=token("alice"))
    assert client.store.calls[-1] == ("search", "n", None)
    assert client.get("/v1/companies/search?q=n&access=mine",
                      headers=token("alice")).status_code == 422


def test_me(client, token):
    body = client.get("/v1/me", headers=token("alice")).json()
    assert body == {"user_id": "alice", "companies": [
        {"company_id": NVDA, "name": "NVIDIA CORP", "sic": "3674",
         "industry": "Semiconductors & Related Devices", "exchanges": ["Nasdaq"],
         "tiers": ["financials"]}]}


def test_financials_link_to_the_filing(token):
    class Store(FakeStore):
        def financials(self, *args):
            return [{**super().financials(*args)[0], "filer_cik": "0001045810"}]

    c = TestClient(create_app(FakeAuthorizer(GRANTS), Store(), SECRET))
    row = c.get(f"/v1/companies/{NVDA}/financials", headers=token("alice")).json()["rows"][0]
    assert row["citation"]["filing_url"] == \
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"


def test_demo_login_is_off_by_default(client):
    assert client.get("/v1/demo/users").status_code == 404
    assert client.post("/v1/demo/token", json={"user_id": "alice"}).status_code == 404


def test_demo_login_only_for_listed_users():
    c = TestClient(create_app(FakeAuthorizer(GRANTS), FakeStore(), SECRET,
                              demo_users=frozenset({"alice", "bob"})))
    assert c.get("/v1/demo/users").json() == {"users": ["alice", "bob"]}
    assert c.post("/v1/demo/token", json={"user_id": "mallory"}).status_code == 404
    token = c.post("/v1/demo/token", json={"user_id": "alice"}).json()["token"]
    me = c.get("/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["user_id"] == "alice"


def test_latest_filings_only_queries_filings_tier_companies(token):
    grants = {"bob": {NVDA: {"financials", "filings"}, TSLA: {"financials"}}}
    store = FakeStore()
    client = TestClient(create_app(FakeAuthorizer(grants), store, SECRET))
    assert client.get("/v1/filings/latest", headers=token("bob")).status_code == 200
    assert store.calls == [("latest_filings", "bob", (NVDA,))]


def test_latest_filings_without_filings_access_skips_the_database(client, token):
    r = client.get("/v1/filings/latest", headers=token("alice"))
    assert r.json()["filings"] == [] and client.store.calls == []


def test_latest_filings_fails_closed_when_openfga_is_down(token):
    client = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), FakeStore(), SECRET))
    assert client.get("/v1/filings/latest", headers=token("alice")).status_code == 503


def test_segments_with_exact_values_and_citations(client, token):
    r = client.get(f"/v1/companies/{NVDA}/segments?axis=product_service&metric=revenue"
                   "&view=ttm&basis=calendar&quarters=12", headers=token("alice"))
    assert r.status_code == 200
    assert client.store.segments_args == ("product_service", "revenue", 12, "ttm", "calendar")
    row = r.json()["rows"][0]
    assert (row["member_label"], row["value"]) == ("Data Center", "89023000000.10")
    assert (row["fiscal_period"], row["calendar_period"]) == ("FY2027 Q2", "CY2026 Q2")
    assert row["citation"]["xbrl_member"] == "nvda:DataCenterMember"
    assert row["citation"]["filing_url"] == \
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000075/"
    assert client.authz.checks == [("alice", "financials", NVDA)]


def test_segments_csv(client, token):
    r = client.get(f"/v1/companies/{NVDA}/segments?format=csv", headers=token("alice"))
    header, line = r.text.splitlines()
    assert header.startswith("axis,member,member_label,metric,view")
    assert "Data Center" in line and "89023000000.10" in line


def test_segments_denied_or_invalid_never_reach_the_database(client, token):
    assert client.get(f"/v1/companies/{TSLA}/segments",
                      headers=token("alice")).status_code == 403
    for query in ("metric=salary", "axis=planet", "view=annual", "quarters=0"):
        assert client.get(f"/v1/companies/{NVDA}/segments?{query}",
                          headers=token("alice")).status_code == 422
    assert client.store.calls == []


def test_status_is_public_and_only_timestamps(client):
    r = client.get("/v1/status")  # no token: outside uptime checks call it
    assert r.status_code == 200
    assert set(r.json()) == {"status", "as_of", "last_accepted_at", "last_ingested_at"}
    assert r.json()["last_accepted_at"] == "2026-10-28T21:12:49Z"
