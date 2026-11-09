"""Route behavior with fake layers: who gets checked, what's refused, and failing closed."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access, AuthzUnavailable

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

    def access(self, user_id):
        return Access(frozenset(), self.readable(user_id))

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

    def cadence(self, user_id, company_id):
        return "quarterly"

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

    def data_status(self):
        self.calls.append(("data_status",))
        # extra columns stand for a SQL function that started returning more than it should
        return {
            "freshness": {**self.feed_status(), "accession_no": "0001045810-26-000075"},
            "ingest": {"window_start": "2026-10-26T15:00:00Z", "filings": 40000,
                       "via_feed": 39000, "p50_seconds": 41.23, "p95_seconds": 170.0,
                       "company_id": NVDA},
            "completeness": {"window_start": date(2026, 10, 3), "days": 0, "indexed": None,
                             "missing": None, "republished": None, "days_with_missing": 0,
                             "last_checked_at": None},
            "coverage": {"companies_with_financials": 6025, "documents": 265625,
                         "indexed_filings": 265000, "sections": 1780000,
                         "last_indexed_at": "2026-11-02T10:00:00Z", "ticker": "NVDA"},
            "quality": [{"check_name": "impossible_value", "severity": "error", "findings": 3,
                         "companies": 2, "companies_checked": 6024,
                         "last_checked_at": "2026-11-02T08:00:00Z", "company_id": NVDA}],
        }

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
        self.calls.append(("latest_filings", user_id,
                           None if company_ids is None else tuple(company_ids)))
        return []

    def search(self, user_id, q, limit=5, only=None, readable_only=False):
        self.calls.append(("search", q, None if only is None else tuple(only)))
        self.search_limit = limit
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


def test_financials_can_ask_for_every_quarter_there_is_but_not_without_a_cap(client, token):
    ok = client.get(f"/v1/companies/{NVDA}/financials?quarters=120", headers=token("alice"))
    assert ok.status_code == 200, ok.text
    too_many = client.get(f"/v1/companies/{NVDA}/financials?quarters=121", headers=token("alice"))
    assert too_many.status_code == 422


def test_a_section_can_be_asked_for_whole_but_not_without_a_cap(client, token):
    ok = client.get(f"/v1/companies/{NVDA}/sections/1A?max_chars=1000000", headers=token("bob"))
    assert ok.status_code != 422
    too_many = client.get(f"/v1/companies/{NVDA}/sections/1A?max_chars=1000001",
                          headers=token("bob"))
    assert too_many.status_code == 422


def test_search_limit_defaults_to_five_and_is_capped(client, token):
    client.get("/v1/companies/search?q=n", headers=token("alice"))
    assert client.store.search_limit == 5
    client.get("/v1/companies/search?q=n&limit=25", headers=token("alice"))
    assert client.store.search_limit == 25
    assert client.get("/v1/companies/search?q=n&limit=26",
                      headers=token("alice")).status_code == 422


def test_me(client, token):
    body = client.get("/v1/me", headers=token("alice")).json()
    assert body == {"user_id": "alice",
                    "catalog": {"public": False, "tiers": [], "companies": 0},
                    "companies": [
        {"company_id": NVDA, "name": "NVIDIA CORP", "sic": "3674",
         "industry": "Semiconductors & Related Devices", "exchanges": ["Nasdaq"],
         "tiers": ["financials"]}]}


# Open access: the public catalog ------------------------------------------------------------

AMD = "0000002488"


class OpenAuthorizer(FakeAuthorizer):
    """catalog:sec public: every user reads the catalog with their tiers; `grants` are the
    private grants (listed individually)."""

    def __init__(self, tiers: dict[str, set[str]], grants):
        super().__init__(grants)
        self.tiers, self.accesses = tiers, 0

    def access(self, user_id):
        self.accesses += 1
        tiers = frozenset(self.tiers.get(user_id, set()))
        return Access(tiers, self.readable(user_id) if tiers else {})


class CatalogStore(FakeStore):
    def __init__(self, ids=frozenset({NVDA, TSLA})):
        super().__init__()
        self.ids, self.catalog_reads = ids, 0

    def catalog_companies(self, user_id):
        self.catalog_reads += 1
        return self.ids


def open_client(clock=None):
    store = CatalogStore()
    authz = OpenAuthorizer({"carol": {"financials", "filings"}, "dave": {"financials"}},
                           {"carol": {AMD: {"financials", "filings"}}})
    c = TestClient(create_app(authz, store, SECRET, monotonic=clock or (lambda: 0.0)))
    c.store, c.authz = store, authz
    return c


def test_me_with_the_catalog_public_lists_only_private_grants(token):
    c = open_client()
    body = c.get("/v1/me", headers=token("carol")).json()
    assert body["catalog"] == {"public": True, "tiers": ["filings", "financials"],
                               "companies": 2}
    assert [x["company_id"] for x in body["companies"]] == [AMD]
    dave = c.get("/v1/me", headers=token("dave")).json()
    assert dave["catalog"]["tiers"] == ["financials"] and dave["companies"] == []
    nobody = c.get("/v1/me", headers=token("erin")).json()  # no tiers: nothing public
    assert nobody["catalog"] == {"public": False, "tiers": [], "companies": 0}


def test_readable_with_the_catalog_public_is_the_catalog_plus_private_grants(token):
    c = open_client()
    c.get("/v1/filings/latest", headers=token("carol"))
    assert c.store.calls[-1] == ("latest_filings", "carol", None)  # public: no id list (RLS)
    n = len(c.store.calls)
    c.get("/v1/filings/latest", headers=token("dave"))  # financials only: no filings
    assert len(c.store.calls) == n  # no query at all
    r = c.get("/v1/companies/search?q=n&access=financials", headers=token("dave")).json()
    assert {x["company_id"]: x["access"] for x in r["results"]} == {
        NVDA: ["financials"], TSLA: ["financials"]}


def test_catalog_list_is_cached_briefly(token):
    now = [0.0]
    c = open_client(lambda: now[0])
    for _ in range(3):
        c.get("/v1/filings/latest", headers=token("carol"))
    assert c.store.catalog_reads == 1 and c.authz.accesses == 3  # OpenFGA every time
    now[0] += 61
    c.get("/v1/filings/latest", headers=token("carol"))
    assert c.store.catalog_reads == 2


def test_financials_say_the_currency_of_per_share_figures_too(token):
    """057: `currency` came from the first exactly-three-letter unit, so a euro company's EPS
    alone ("EUR/shares") read as dollars. Any amount's unit says it now; dollars stay unsaid."""
    unit = {"value": "EUR/shares"}

    class Store(FakeStore):
        def financials(self, *args):
            return [{**super().financials(*args)[0], "metric": "eps_diluted", "value": 1.22,
                     "unit": unit["value"]}]

    c = TestClient(create_app(FakeAuthorizer(GRANTS), Store(), SECRET))
    url = f"/v1/companies/{NVDA}/financials?metrics=eps_diluted"
    assert c.get(url, headers=token("alice")).json()["currency"] == "EUR"
    unit["value"] = "USD/shares"
    assert "currency" not in c.get(url, headers=token("alice")).json()


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


def test_status_data_is_public_and_aggregate_only(client):
    r = client.get("/v1/status/data")  # no token: the public status page
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"as_of", "max_age_seconds", "freshness", "ingest", "completeness",
                         "coverage", "quality", "recorded"}
    assert body["ingest"] == {"window_days": 7, "window_start": "2026-10-26T15:00:00Z",
                              "filings": 40000, "via_feed": 39000, "live_pct": 97.5,
                              "p50_seconds": 41.2, "p95_seconds": 170.0}
    assert body["coverage"]["companies_with_financials"] == 6025
    assert body["quality"] == {"companies_checked": 6024,
                               "last_checked_at": "2026-11-02T08:00:00Z",
                               "checks": [{"check": "impossible_value", "severity": "error",
                                           "findings": 3, "companies": 2}]}
    # nothing the store returned beyond the named fields reaches the public route
    text = r.text
    assert NVDA not in text and "0001045810-26-000075" not in text and "NVDA" not in text
    assert set(body["recorded"]) == {"adversarial", "citations", "golden"}
    assert body["recorded"]["adversarial"]["leaks"] == 0


def test_status_data_before_the_first_completeness_check(client):
    c = client.get("/v1/status/data").json()["completeness"]
    assert c == {"window_days": 30, "window_start": "2026-10-03", "days_checked": 0,
                 "indexed": None, "missing": None, "republished": None,
                 "days_with_missing": 0, "last_checked_at": None}


def test_status_data_is_cached_for_a_minute():
    from datetime import UTC, datetime, timedelta

    t = [datetime(2026, 11, 2, 12, 0, tzinfo=UTC)]
    store = FakeStore()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET, clock=lambda: t[0]))
    first = c.get("/v1/status/data").json()
    t[0] += timedelta(seconds=59)
    assert c.get("/v1/status/data").json()["as_of"] == first["as_of"]
    assert store.calls == [("data_status",)]
    t[0] += timedelta(seconds=1)
    assert c.get("/v1/status/data").json()["as_of"] == "2026-11-02T12:01:00+00:00"
    assert store.calls == [("data_status",)] * 2


@pytest.mark.parametrize(
    ("facts", "latest_form", "cadence"),
    [
        # (recent quarter, any quarter, years, any figure)
        ((True, True, True, True), "10-Q", "quarterly"),
        # EIDP: still files 10-Qs, its figures end in 2019: old, never "once a year"
        ((False, True, True, True), "10-Q", "quarterly"),
        ((False, True, True, True), "10-K", "quarterly"),
        ((False, False, True, True), "10-K", "quarterly"),  # a 10-K with no quarter facts
        # Birkenstock: a 20-F filer's years
        ((False, False, True, True), "20-F", "annual"),
        ((False, True, True, True), "40-F/A", "annual"),  # quarters once, years now
        # ICON, Arm: 20-F filers with quarters (6-Ks) in gold read as quarters
        ((True, True, True, True), "20-F", "quarterly"),
        # nothing in the index: the facts' period lengths decide
        ((False, False, True, True), None, "annual"),
        ((False, True, True, True), None, "quarterly"),
        ((False, False, False, True), "20-F", "quarterly"),  # balances only, no years
        ((False, False, False, False), None, None),
    ],
)
def test_cadence_comes_from_forms_and_period_lengths_never_from_staleness(facts, latest_form,
                                                                         cadence):
    from query_api.store import cadence_of

    recent, quarters, years, figures = facts
    assert cadence_of(recent=recent, quarters=quarters, years=years, figures=figures,
                      latest_form=latest_form) == cadence
