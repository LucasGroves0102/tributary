"""Peers by business (query_api/peers.py, the spec) and the routes around it, with fake layers:
/peers ranking and its words, a bank's columns, /peers/check reasons, /v1/sectors and the
screener's co-registrants (#135)."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api import peers as P
from query_api.app import create_app

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_screener import meta
from .test_ui_api import PeerStore

AMD, SHELL, TINY, OTHER = "0000002488", "0009999999", "0001111111", "0002222222"


def cand(cid, sic="3674", sim=0.2, **kw):
    return P.Candidate(cid, sic, sim, ["gpu"], **kw)


def test_division_is_a_weak_signal():
    assert P.sic_match("3674", "3674") == 4 and P.sic_match("3674", "3672") == 3
    assert P.sic_match("3674", "3699") == 2 and P.sic_match("3679", "3590") == 1
    assert P.sic_match("3679", "7374") == 0 and P.sic_match(None, "3674") is None
    assert P.division("6021") == "H" and P.division("7372") == "I" and P.division("") is None
    # a maker with less similar text still beats a customer in another division by a little
    ranked = P.rank("3679", None, [cand("A", "7374", 0.090), cand("B", "3590", 0.080)])
    assert [c.company_id for c in ranked] == ["B", "A"]


def test_size_counts_only_where_both_revenues_are_readable():
    big, small = Decimal("13e9"), Decimal("13e6")
    assert P.size_factor(big, big, True) == 1.0
    assert round(P.size_factor(big, Decimal("1.3e9"), True), 3) == round(1 / 1.3, 3)
    assert P.size_factor(big, small, False) == 1.0          # unreadable: no oracle
    assert P.size_factor(None, small, True) == 1.0          # target's own size unknown
    assert P.size_factor(big, None, True) == P.NO_REVENUE    # readable, no figure
    assert P.size_factor(big, Decimal(1), True) == P.size_factor(big, P.SIZE_FLOOR, True)


def test_competitors_named_lift_a_peer_and_shells_never_appear():
    ranked = P.rank("3674", Decimal("1e11"), [
        cand(AMD, sim=0.12, named_by_target=True, revenue=Decimal("4e10"), readable=True),
        cand(OTHER, sim=0.14, revenue=Decimal("1e11"), readable=True),
        cand(SHELL, sic="6770", sim=0.5),
        cand(TINY, sim=0.12, revenue=Decimal("1e6"), readable=True)])
    assert [c.company_id for c in ranked] == [AMD, OTHER, TINY]
    assert ranked[0].score == pytest.approx(0.12 * 1.15 / (1 + 0.3 * 0.39794) + P.NAMED, 1e-4)
    assert ranked[1].score == pytest.approx(0.14 * 1.15)  # same size, not named
    assert ranked[2].score == pytest.approx(0.12 * 1.15 / (1 + 0.3 * 4))  # floored at $10M
    # a shell's peers may be shells
    assert [c.company_id for c in P.rank("6770", None, [cand(SHELL, sic="6770")])] == [SHELL]


class BusinessStore(PeerStore):
    """NVDA's stored neighbours: AMD (named in NVDA's Item 1), TSLA, a blank check; TSLA's Item 1
    names NVDA too."""

    def __init__(self, bank=False):
        super().__init__()
        self.bank = bank

    def screen(self, user_id, compiled):
        rows = super().screen(user_id, compiled)
        if AMD in compiled.params.get("ids", []):  # values for every company, as RLS off
            rows.append({**meta(AMD, "ADVANCED MICRO DEVICES INC"), "total": 3,
                         **{f"v{i}": Decimal("3e10") for i in compiled.slots.values()},
                         **{f"src{i}": {"period_end": "2026-09-27", "accession_no": "m",
                                        "form": "10-Q", "filed": "2026-11-04"}
                            for i in compiled.slots.values()}})
        for r in rows:
            r["not_applicable"] = ["gross_margin"] if self.bank else []
            r["sector"], r["sector_industry"] = "Technology", "Semiconductors"
        return rows

    def business_peers(self, user_id, company_id):
        self.calls.append(("business_peers", company_id))
        return {"profile": {"accession_no": "0001045810-26-000021", "section_key": "1",
                            "filed": date(2026, 2, 25), "terms": ["gpu", "accelerated computing"],
                            "computed_at": None},
                "peers": [{"peer_id": TSLA, "similarity": 0.12, "terms": ["autonomous driving"]},
                          {"peer_id": SHELL, "similarity": 0.4, "terms": ["acquisition"]},
                          {"peer_id": AMD, "similarity": 0.25, "terms": ["gpu", "cpu"]}],
                "mentions": [{"company_id": NVDA, "named_id": AMD, "accession_no": "a",
                              "section_key": "1", "char_start": 10, "char_end": 90},
                             {"company_id": TSLA, "named_id": NVDA, "accession_no": "t",
                              "section_key": "1", "char_start": 5, "char_end": 50}]}

    def peer_facts(self, user_id, ids, granted):
        self.calls.append(("peer_facts", tuple(ids), granted))
        sic = {AMD: "3674", TSLA: "3711", SHELL: "6770"}
        return {c: {"sic": sic[c], "sector": "Technology" if c == AMD else "Other",
                    "sector_industry": "Semiconductors" if c == AMD else "Other",
                    "filer_category": "large_accelerated", "revenue": Decimal("3e10")}
                for c in ids}


def client(grants, store=None):
    s = store or BusinessStore()
    c = TestClient(create_app(FakeAuthorizer(grants), s, SECRET))
    c.store = s
    return c


def test_business_peers_rank_say_why_and_cite(token):
    c = client({"alice": {NVDA: {"financials", "filings"}, AMD: {"financials"}}})
    r = c.get(f"/v1/companies/{NVDA}/peers", headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["basis"] == "business" and body["business"]["terms"][0] == "gpu"
    assert [p["company_id"] for p in body["peers"]] == [AMD, TSLA]  # the shell is dropped
    amd, tsla = (p["match"] for p in body["peers"])
    assert amd["named_by_target"] == {"accession_no": "a", "section_key": "1",
                                      "char_start": 10, "char_end": 90}
    assert amd["terms"] == ["gpu", "cpu"] and amd["similarity"] == 0.25
    assert amd["same_industry"] is True and amd["sic_level"] == 4
    assert tsla["names_target"]["accession_no"] == "t" and tsla["named_by_target"] is None
    # sizes only for what alice reads: AMD has a band, TSLA none, and no oracle in the score
    assert amd["revenue_band"] == "$10B-$100B" and tsla["revenue_band"] is None
    assert tsla["same_revenue_band"] is False
    assert "Business sections" in body["method"]


def test_peers_need_financials_on_the_company_first(token):
    c = client({"alice": {TSLA: {"financials"}}})
    assert c.get(f"/v1/companies/{NVDA}/peers", headers=token("alice")).status_code == 403
    assert c.store.calls == []


def test_only_readable_peers_when_asked(token):
    c = client({"alice": {NVDA: {"financials", "filings"}, AMD: {"financials"}}})
    body = c.get(f"/v1/companies/{NVDA}/peers?access=financials", headers=token("alice")).json()
    assert [p["company_id"] for p in body["peers"]] == [AMD]


def test_a_bank_is_compared_on_bank_columns(token):
    c = client({"alice": {NVDA: {"financials", "filings"}}}, BusinessStore(bank=True))
    body = c.get(f"/v1/companies/{NVDA}/peers", headers=token("alice")).json()
    keys = [col["key"] for col in body["columns"]]
    assert keys[:3] == ["revenue_ttm", "net_interest_income_ttm", "efficiency_ratio_ttm"]
    assert "loans_q" in keys and "deposits_q" in keys and "gross_margin_ttm" not in keys
    asked = c.get(f"/v1/companies/{NVDA}/peers?columns=revenue_ttm",
                  headers=token("alice")).json()
    assert [col["key"] for col in asked["columns"]] == ["revenue_ttm"]  # what was asked wins


class CheckStore(BusinessStore):
    REPORTS = {AMD: {"has_financials": True, "sic": "3674", "annual": {"10-K": "2026-02-04"}},
               "0001977102": {"has_financials": False, "sic": "3140",
                              "annual": {"20-F": "2025-12-10"}},
               "0001234567": {"has_financials": False, "sic": "7372",
                              "annual": {"10-K": "2026-03-01"}},
               "0007654321": {"has_financials": False, "sic": "6770", "annual": None}}

    def annual_reports(self, user_id, company_id):
        return self.REPORTS.get(company_id, {"has_financials": False, "sic": None,
                                             "annual": None})


@pytest.mark.parametrize("candidate,ok,reason,form", [
    (AMD, True, None, "10-K"),
    ("0001977102", False, "foreign_filer", "20-F"),   # Birkenstock
    ("0001234567", False, "no_xbrl", "10-K"),
    ("0007654321", False, "no_annual_report", None)])
def test_peer_check_says_why_a_company_cannot_join(token, candidate, ok, reason, form):
    c = client({"alice": {NVDA: {"financials"}, AMD: {"financials"}}}, CheckStore())
    r = c.get(f"/v1/companies/{NVDA}/peers/check/{candidate}", headers=token("alice"))
    assert r.status_code == 200, r.text
    assert (r.json()["ok"], r.json()["reason"], r.json()["annual_form"]) == (ok, reason, form)


def test_peer_check_metadata_only_and_refusals(token):
    c = client({"alice": {NVDA: {"financials"}}}, CheckStore())
    r = c.get(f"/v1/companies/{NVDA}/peers/check/{AMD}", headers=token("alice")).json()
    assert (r["ok"], r["reason"]) == (True, "metadata_only")
    assert c.get(f"/v1/companies/{NVDA}/peers/check/0000000009",
                 headers=token("alice")).status_code == 404
    assert c.get(f"/v1/companies/{TSLA}/peers/check/{AMD}",
                 headers=token("alice")).status_code == 403


class SectorStore(CheckStore):
    def sectors(self, user_id):
        return [{"sector": "Technology", "industry": "Semiconductors", "sic_from": "3674",
                 "sic_to": "3674", "companies": 100},
                {"sector": "Technology", "industry": "Electronic components", "sic_from": "3670",
                 "sic_to": "3673", "companies": 10},
                {"sector": "Technology", "industry": "Electronic components", "sic_from": "3675",
                 "sic_to": "3679", "companies": 30}]


def test_sectors_route_groups_ranges(token):
    c = client({}, SectorStore())
    body = c.get("/v1/sectors", headers=token("carol")).json()
    (tech,) = body["sectors"]
    assert tech["companies"] == 140
    assert tech["industries"][0] == {"name": "Electronic components", "sic": ["3670-3673",
                                     "3675-3679"], "companies": 40}
    assert "not GICS" in body["note"]


def test_screener_rows_carry_co_registrants_and_sector_filters(token):
    c = client({"alice": {NVDA: {"financials"}}})
    r = c.get("/v1/screener?sector=Technology&sector_industry=Semiconductors&sort=sector",
              headers=token("alice"))
    assert r.status_code == 200, r.text
    compiled = c.store.compiled[-1]
    assert compiled.params["sectors"] == ["Technology"]
    assert compiled.params["sector_industries"] == ["Semiconductors"]
    assert "ss.sector = ANY(%(sectors)s)" in compiled.sql and "Technology" not in compiled.sql
    rows = {x["company_id"]: x for x in r.json()["rows"]}
    assert rows[NVDA]["co_registrants"][0]["company_id"] == TSLA
    assert rows[TSLA]["co_registrants"] == []
