"""GET /v1/companies/{id}/insiders (#117): layer 1 first, the shape the web app words, bounded."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.insiders import roles, summary

from .conftest import SECRET
from .test_research_api import GRANTS, NVDA, TSLA, FakeAuthorizer

KRESS = "0001588670-26-000014"


def line(n, kind="transaction", code="S", shares="100", price="200", **extra):
    return {"line": n, "kind": kind, "derivative": False, "security_title": "Common",
            "transaction_date": date(2026, 9, 17), "code": code, "equity_swap": False,
            "shares": Decimal(shares) if shares else None,
            "price": Decimal(price) if price else None, "acquired_disposed": "D",
            "shares_after": Decimal("795340"), "direct_indirect": "D", "nature": None,
            "exercise_price": None, "underlying_title": None, "underlying_shares": None, **extra}


class Store:
    def __init__(self):
        self.calls = []

    def insider_filings(self, user_id, company_id, limit, before=None):
        self.calls.append((user_id, company_id, limit, before))
        return [{"accession_no": KRESS, "issuer_cik": NVDA, "form": "4", "filed": date(2026, 9, 18),
                 "period_of_report": date(2026, 9, 16), "aff10b5_one": True,
                 "owners": [{"cik": "0001588670", "name": "Kress Colette", "director": False,
                             "officer": True, "title": "EVP & Chief Financial Officer",
                             "ten_percent": False, "other": False, "other_text": None}],
                 "lines": [line(1, code="F", shares="40747", price="212.17"),
                           line(2, shares="4681", price="218.0144"),
                           line(3, kind="holding", code=None, shares=None, price=None)]}] * limit


def client():
    store = Store()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET))
    c.store = store  # type: ignore[attr-defined]
    return c


def test_insiders_are_checked_first_and_shaped(token):
    c = client()
    assert c.get(f"/v1/companies/{TSLA}/insiders", headers=token("bob")).status_code == 403
    assert c.store.calls == []  # type: ignore[attr-defined]
    r = c.get(f"/v1/companies/{NVDA}/insiders?limit=2", headers=token("bob"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_more"] is True and len(body["filings"]) == 2
    f = body["filings"][0]
    assert f["owners"] == [{"cik": "0001588670", "name": "Kress Colette",
                            "roles": ["EVP & Chief Financial Officer"]}]
    sale = f["lines"][1]
    assert (sale["code"], sale["code_label"], sale["shares"], sale["price"], sale["value"]) == (
        "S", "Open-market sale", "4681", "218.0144", "1020525.4064")
    assert f["lines"][2]["value"] is None  # a holding has no value
    assert f["summary"] == {"other": {"shares": "40747", "value": "8645290.99"},
                            "sold": {"shares": "4681", "value": "1020525.4064"}}
    assert f["citation"]["filing_url"] == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000158867026000014/")
    assert c.store.calls[-1] == ("bob", NVDA, 3, None)  # type: ignore[attr-defined]


def test_insiders_without_filings_access_and_bad_input(token):
    c = client()
    # alice reads NVDA's financials only: insider filings are filing content
    assert c.get(f"/v1/companies/{NVDA}/insiders", headers=token("alice")).status_code == 403
    for q in ("limit=0", "limit=101", "before=soon"):
        assert c.get(f"/v1/companies/{NVDA}/insiders?{q}", headers=token("bob")).status_code == 422
    assert c.get("/v1/companies/NVDA/insiders", headers=token("bob")).status_code == 422
    assert c.store.calls == []  # type: ignore[attr-defined]


def test_roles_and_summary_rules():
    assert roles({"director": True, "officer": True, "title": None, "ten_percent": True,
                  "other": True, "other_text": "Member of 13(d) group"}) == [
        "Director", "Officer", "10% owner", "Member of 13(d) group"]
    # an unpriced line makes its kind's value unknown, never a wrong total
    assert summary([line(1, code="P", price=None), line(2, code="P")]) == {
        "bought": {"shares": "200", "value": None}}
