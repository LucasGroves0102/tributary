"""POST /v1/companies/{id}/model (model_api.py) with fake layers: the financials tier checked
first and the same 403 either way, the strict state, Store read as the caller, a JSON answer."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import FakeAuthorizer

VRT, NVDA = "0001674101", "0001045810"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "model" / "vrt.json").read_text())
GRANTS = {"bob": {VRT: {"financials"}}, "carol": {VRT: {"filings"}}}
STATE = {"companyId": VRT, "historyYears": 8, "ytd": True,
         "assumptions": [{"growth": 50, "margin": 25, "note": "Q4 guide"},
                         {"growth": 20, "margin": 22, "taxRate": 21}],
         "adjustments": [{"name": "UIG", "fy": 2026, "quarter": 4, "kind": "acquired",
                          "amount": 150e6}],
         "price": {"value": 150, "date": "2026-11-06"}}


class Store:
    def __init__(self):
        self.calls = []

    def model_inputs(self, user_id, company_id, metrics, quarters, recent, years, latest):
        self.calls.append((user_id, company_id, quarters, recent, latest))
        return json.loads(json.dumps(FIXTURE))


def client(store=None):
    store = store or Store()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET))
    c.store = store
    return c


def test_a_run_reads_as_the_caller_and_answers_every_case(token):
    c = client()
    r = c.post(f"/v1/companies/{VRT}/model", json=STATE, headers=token("bob"))
    assert r.status_code == 200
    assert c.store.calls == [("bob", VRT, 40, 12, ["net_debt", "shares_outstanding"])]
    body = r.json()
    assert body["company_id"] == VRT and body["base_fy"] == 2025 and body["ytd"] is True
    assert set(body["cases"]) == {"bear", "base", "bull"}
    assert body["cases"]["bear"]["preview"] is True
    y1 = body["cases"]["base"]["years"][0]
    assert y1["note"] == "Q4 guide" and y1["actual"]["quarters"] == 3
    assert y1["organic_growth"] < y1["growth"]
    assert body["valuation"]["price"] == 150 and body["valuation"]["date"] == "2026-11-06"
    # citations carry their EDGAR filing
    src = body["history"][-1]["sources"]["revenue"]
    assert src["filing_url"].startswith("https://www.sec.gov/Archives/edgar/data/1674101/")
    assert body["valuation"]["net_debt"]["citation"]["filing_url"].startswith(
        "https://www.sec.gov/Archives/edgar/data/1674101/")
    json.dumps(body, allow_nan=False)


def test_per_share_history_is_on_one_share_basis(token):
    """Riley, test 10, finding 6: FY2022 read $3.85 on 2.54B shares beside FY2023's $0.17 on
    25.07B. With per_share's inputs every year is on today's basis, the 10-K's figure kept."""
    model = json.loads((Path(__file__).parent / "fixtures" / "model" / "nvda.json").read_text())
    shares = json.loads(
        (Path(__file__).parent / "fixtures" / "per_share" / "nvda.json").read_text())

    class NvdaStore(Store):
        def model_inputs(self, *args):
            return {**json.loads(json.dumps(model)), "per_share": shares}

    state = {"companyId": NVDA, "historyYears": 8, "ytd": True,
             "assumptions": [{"growth": 20, "margin": 50}]}
    c = TestClient(create_app(FakeAuthorizer({"bob": {NVDA: {"financials"}}}), NvdaStore(), SECRET))
    r = c.post(f"/v1/companies/{NVDA}/model", json=state, headers=token("bob"))
    assert r.status_code == 200, r.text
    years = {y["fy"]: y for y in r.json()["history"]}
    assert round(years[2022]["eps"], 4) == 0.385 and years[2023]["eps"] == 0.17
    assert 24e9 < years[2022]["shares"] < 26e9 and 24e9 < years[2023]["shares"] < 26e9
    assert not any(y["share_break"] for y in years.values())
    assert years[2022]["sources"]["eps_diluted"]["filing_url"].startswith(
        "https://www.sec.gov/Archives/edgar/data/1045810/")
    # a recorded answer without per_share's inputs keeps the 10-Ks' own figures
    plain = client().post(f"/v1/companies/{VRT}/model", json=STATE, headers=token("bob"))
    assert plain.status_code == 200


def test_permission_first_and_the_same_answer_for_any_company(token):
    c = client()
    no_tier = c.post(f"/v1/companies/{VRT}/model", json=STATE, headers=token("carol"))
    other = c.post(f"/v1/companies/{NVDA}/model", json={**STATE, "companyId": NVDA},
                   headers=token("bob"))
    assert no_tier.status_code == other.status_code == 403
    assert no_tier.json() == other.json() == {
        "detail": "not permitted to read financials for this company"}
    assert c.store.calls == []
    assert c.post(f"/v1/companies/{VRT}/model", json=STATE).status_code == 401


def test_the_state_is_checked_strictly(token):
    c = client()
    url = f"/v1/companies/{VRT}/model"
    bad = [
        {**STATE, "companyId": NVDA},  # another company than the path's
        {**STATE, "assumptions": []},
        {**STATE, "history": [{"revenue": 1}]},  # history is never sent or stored
        {**STATE, "price": {"value": 0, "date": "2026-11-06"}},
        {**STATE, "price": {"value": 10, "date": "2026-02-30"}},
        {**STATE, "adjustments": [{"name": "x", "fy": 2026, "quarter": 5, "amount": 1}]},
        {**STATE, "assumptions": [{"growth": 1, "margin": 1, "shareChange": -60}]},
    ]
    for state in bad:
        r = c.post(url, json=state, headers=token("bob"))
        assert r.status_code == 422, state
    assert c.store.calls == []


def test_a_large_body_is_refused_before_it_is_read(token):
    r = client().post(f"/v1/companies/{VRT}/model", content=b"{" + b" " * 140_000 + b"}",
                      headers={**token("bob"), "Content-Type": "application/json"})
    assert r.status_code == 413
