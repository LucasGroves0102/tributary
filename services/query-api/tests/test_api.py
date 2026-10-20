"""Route behavior with fake layers: who gets checked, what's refused, and failing closed."""

from datetime import date

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

    def sections(self, *args):
        self.calls.append(("sections",) + args[:2])
        return []

    def filings(self, *args):
        self.calls.append(("filings",) + args[:2])
        return []

    def latest_filings(self, user_id, company_ids, limit):
        self.calls.append(("latest_filings", user_id, tuple(company_ids)))
        return []

    def search(self, user_id, q):
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
