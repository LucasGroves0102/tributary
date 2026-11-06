"""With the catalog public, cross-company routes must not pass the catalog's ~6,000 ids into SQL:
that list comes from authz.public_access, which RLS reads anyway, so it filters nothing and the
queries around it timed out (hermes, 2026-11-02; migration 032). Fails if a route goes back to it.
Also: layer 1 still blanks what it doesn't allow when no id list reaches the query."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access, Readable
from query_api.shapes import newest_windows
from query_api.store import SearchTooBroad

from .conftest import SECRET
from .test_api import NVDA, TSLA

CATALOG = frozenset(f"{i:010d}" for i in range(1, 6034)) | {NVDA, TSLA}
OUTSIDE = "9000000009"  # not in CATALOG
TOO_MANY = 100  # any list this long in a query's parameters is the catalog going back in
TIERS = frozenset({"financials", "filings"})
TODAY = date(2026, 11, 6)
ROUTES = ["/v1/screener?limit=50", "/v1/screener?limit=50&access=financials",
          "/v1/screener?where=revenue_ttm:gte:1", "/v1/filings/latest", "/v1/filing-index?limit=50",
          "/v1/search?q=supply+chain", "/v1/companies/search?q=app&access=financials",
          "/v1/data-notes", f"/v1/companies/{NVDA}/peers", "/v1/watchlist",
          "/v1/calendar/expected-filings?scope=readable"]


def meta(cid):
    return {"company_id": cid, "name": f"Co {cid}", "tickers": ["X"], "exchanges": ["Nasdaq"],
            "sic": "3674", "industry": "Semiconductors", "fiscal_year_end": "0126",
            "filer_category": "large_accelerated", "filer_categories": [],
            "state_of_incorporation": "DE"}


class RecordingStore:
    """Records every call and answers with rows for NVDA and OUTSIDE (not in the catalog, no
    grant: as if RLS let it through). Layer 1 must still blank its values and section counts."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def catalog_companies(self, user_id):
        return CATALOG

    def __getattr__(self, name: str):
        if name.startswith("answer_"):
            raise AttributeError(name)

        def call(*args: Any, **kwargs: Any):
            self.calls.append((name, args, kwargs))
            return getattr(self, f"answer_{name}", lambda *_: [])(*args)
        return call

    def answer_screen(self, user_id, compiled):
        self.calls.append(("screen.params", (compiled.params,), {}))
        return [{**meta(c), "total": 2,
                 **{f"v{i}": Decimal("7") for i in compiled.slots.values()},
                 **{f"src{i}": {"accession_no": "a"} for i in compiled.slots.values()}}
                for c in (NVDA, OUTSIDE)]

    def answer_filing_index(self, user_id, ids, forms, start, end, limit, offset, sections_for):
        return [{"accession_no": f"{c}-26-000001", "company_id": c, "name": "Co", "cik": c,
                 "form": "8-K", "filing_date": date(2026, 11, 5), "filed": date(2026, 11, 5),
                 "accepted_at": None, "period_of_report": None, "primary_document": "d.htm",
                 "sections": 3} for c in (NVDA, OUTSIDE)] * limit

    def answer_data_notes(self, *_):
        return {"findings": [{"company_id": c, "check_name": "series_gap", "severity": "warning",
                              "metric": "revenue", "period_end": date(2026, 1, 1), "detail": {},
                              "checked_at": None} for c in (NVDA, OUTSIDE)],
                "derived": [], "behind": [], "checked_at": None}

    def answer_latest_filings(self, *_):
        return [{"company_id": c, "accession_no": "a"} for c in (NVDA, OUTSIDE)]

    def answer_search_sections(self, user_id, ids, q, form, section, start, end, limit, offset):
        return [{"company_id": c, "name": "N", "section_key": "1A", "title": "T", "rank": 0.5,
                 "snippet": "s", "passages_matched": 1, "accession_no": "a", "form": "10-K",
                 "filed": "2026-11-01", "period_of_report": None, "document_url": "u", "cik": c,
                 "char_start": 0, "char_end": 1, "ingested_at": "2026-11-01"}
                for c in (NVDA, OUTSIDE)] if q != "rare words" else []

    def answer_last_reports(self, *_):
        return {}

    def answer_peers(self, *_, **__):
        return []


class Watchlist:
    def list(self, user_id):
        return [{"company_id": NVDA, "added_at": "2026-01-01"}]


class Open:
    """OpenFGA with the catalog public to everyone, within `tiers`."""

    def __init__(self, tiers=TIERS, grants=None):
        self.tiers, self.grants = tiers, grants or {}

    def can(self, user_id, tier, company_id):
        return tier in self.tiers and company_id in CATALOG

    def access(self, user_id):
        return Access(self.tiers, self.grants)


def app(authorizer=None) -> Any:
    store: Any = RecordingStore()
    watchlists: Any = Watchlist()
    c: Any = TestClient(create_app(authorizer or Open(), store, SECRET, watchlists=watchlists,
                                   today=lambda: TODAY))
    c.store = store
    return c


def long_lists(value: Any) -> list[int]:
    if isinstance(value, (list, tuple, set, frozenset)):
        return [len(value)] if len(value) >= TOO_MANY else [
            n for v in value for n in long_lists(v)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in long_lists(v)]
    return []


@pytest.mark.parametrize("route", ROUTES)
def test_no_route_passes_the_catalog_ids_into_sql(token, route):
    c = app()
    r = c.get(route, headers=token("carol"))
    assert r.status_code == 200, r.text
    assert c.store.calls, "the route should have queried"
    for name, args, kwargs in c.store.calls:
        if name == "catalog_companies":
            continue
        assert not long_lists([args, kwargs]), f"{route}: {name} got a catalog-sized list"


def test_the_test_would_catch_it():
    assert long_lists([("user", sorted(CATALOG))]) == [len(CATALOG)]
    assert long_lists({"params": {"granted": sorted(CATALOG)}}) == [len(CATALOG)]


def test_catalog_public_only_at_one_tier_still_lists_that_tier(token):
    """A plan without `filings`: nothing public at that tier, so its routes don't query at all
    (and don't get to treat 'no list' as 'everything')."""
    c = app(Open(frozenset({"financials"})))
    assert c.get("/v1/filings/latest", headers=token("dave")).json()["filings"] == []
    r = c.get("/v1/search?q=supply+chain", headers=token("dave"))
    assert r.status_code == 200 and r.json()["results"] == []
    assert not c.store.calls


def test_company_search_narrows_with_the_rule_not_a_list(token):
    c = app()
    c.get("/v1/companies/search?q=app&access=financials", headers=token("carol"))
    assert c.store.calls[-1][2] == {"only": None, "readable_only": True}
    c.get("/v1/companies/search?q=app", headers=token("carol"))
    assert c.store.calls[-1][2] == {"only": None, "readable_only": False}


def test_layer_one_still_blanks_values_outside_what_it_allows(token):
    """No id list reached the query and the store returned OUTSIDE's values anyway: blanked here."""
    c = app()
    rows = c.get("/v1/screener?limit=50", headers=token("carol")).json()["rows"]
    by_id = {r["company_id"]: r for r in rows}
    assert by_id[NVDA]["values"]["revenue_ttm"] == "7"
    assert by_id[OUTSIDE]["values"]["revenue_ttm"] is None
    assert by_id[OUTSIDE]["sources"]["revenue_ttm"] is None


def test_layer_one_drops_rows_it_does_not_allow_from_cross_company_lists(token):
    """As if RLS let OUTSIDE through: it never reaches the caller, whichever route."""
    c = app()
    for route, rows in (("/v1/filings/latest", "filings"), ("/v1/search?q=x+y&limit=5", "results"),
                        ("/v1/data-notes", "notes")):
        body = c.get(route, headers=token("carol")).json()
        assert [r["company_id"] for r in body[rows]] == [NVDA], route
    filings = c.get("/v1/filing-index?limit=2", headers=token("carol")).json()["filings"]
    assert {f["company_id"]: f["sections"] for f in filings} == {NVDA: 3, OUTSIDE: None}


def test_a_private_grant_beyond_the_catalog_keeps_its_values(token):
    c = app(Open(TIERS, {OUTSIDE: set(TIERS)}))
    rows = c.get("/v1/screener?limit=50", headers=token("carol")).json()["rows"]
    by_id = {r["company_id"]: r for r in rows}
    assert by_id[OUTSIDE]["values"]["revenue_ttm"] == "7"
    assert by_id[OUTSIDE]["access"] == ["filings", "financials"]


def test_filing_index_section_counts_only_for_filings_tier(token):
    c = app()
    c.get("/v1/filing-index?limit=2", headers=token("carol"))
    assert c.store.calls[-1][1][7] is None  # sections_for: every company, RLS alone
    c = app(Open(frozenset({"financials"})))
    c.get("/v1/filing-index?limit=2", headers=token("dave"))
    assert c.store.calls[-1][1][7] == []  # no filings tier: no counts at all


def test_filing_index_stops_at_the_first_window_that_fills_the_page(token):
    c = app()
    assert c.get("/v1/filing-index?limit=2", headers=token("carol")).json()["has_more"] is True
    assert [args[3] for name, args, _ in c.store.calls if name == "filing_index"] == [
        date(2026, 11, 3)]


def test_filing_index_widens_until_it_is_full_then_all_time(token):
    c = app()
    c.store.answer_filing_index = lambda *_: []
    r = c.get("/v1/filing-index?limit=2", headers=token("carol"))
    assert r.status_code == 200 and r.json()["filings"] == []
    assert [args[3] for name, args, _ in c.store.calls if name == "filing_index"] == [
        date(2026, 11, 3), date(2026, 10, 23), date(2026, 8, 8), date(2025, 11, 6), None]


def test_filing_index_of_named_companies_stays_a_short_list(token):
    c = app()
    c.get(f"/v1/filing-index?company_id={NVDA}", headers=token("carol"))
    name, args, _ = c.store.calls[-1]
    assert name == "filing_index" and args[1] == [NVDA] and args[7] == [NVDA]
    assert args[3] is None  # exact, no window


def test_search_reports_the_window_it_ranked(token):
    c = app()
    body = c.get("/v1/search?q=supply+chain&limit=1", headers=token("carol")).json()
    assert body["has_more"] is True and body["searched_from"].startswith("20")
    assert [a[1] for n, a, _ in c.store.calls if n == "search_sections"] == [None]


def test_search_widens_to_all_time_when_few_matches(token):
    c = app()
    body = c.get("/v1/search?q=rare+words", headers=token("carol")).json()
    calls = [a for n, a, _ in c.store.calls if n == "search_sections"]
    assert len(calls) == 4 and calls[-1][5] is None  # 30, 180, 730 days, then all time
    assert body["searched_from"] is None and body["results"] == []


def test_search_with_from_stops_there_and_too_broad_is_still_422(token):
    c = app()
    c.get("/v1/search?q=x+y&from=2026-10-20&to=2026-11-01", headers=token("carol"))
    calls = [a for n, a, _ in c.store.calls if n == "search_sections"]
    assert [a[5] for a in calls] == [date(2026, 10, 20)]  # 30 days back is before `from`

    def broad(*_):
        raise SearchTooBroad
    c.store.answer_search_sections = broad
    assert c.get("/v1/search?q=revenue", headers=token("carol")).status_code == 422


def test_search_of_one_company_is_exact_all_time(token):
    c = app()
    c.get(f"/v1/search?q=revenue&company={NVDA}", headers=token("carol"))
    [(_, args, _)] = [x for x in c.store.calls if x[0] == "search_sections"]
    assert args[1] == [NVDA] and args[5] is None


def test_readable_ids():
    r = Readable({"a": {"financials"}, "b": {"filings"}}, frozenset({"financials"}))
    assert r.ids("financials") is None
    assert r.ids("filings") == ["b"]
    assert Readable({"b": {"filings"}, "a": {"filings"}}).ids("filings") == ["a", "b"]
    assert Readable({}).ids("filings") == []


def test_newest_windows():
    today = date(2026, 11, 6)
    assert list(newest_windows((3, 14), None, None, today)) == [
        date(2026, 11, 3), date(2026, 10, 23), None]
    assert list(newest_windows((3, 14), date(2026, 10, 30), None, today)) == [
        date(2026, 11, 3), date(2026, 10, 30)]
    assert list(newest_windows((3, 14), None, date(2025, 1, 15), today)) == [
        date(2025, 1, 12), date(2025, 1, 1), None]
    assert list(newest_windows((3,), date(2026, 11, 5), None, today)) == [date(2026, 11, 5)]
