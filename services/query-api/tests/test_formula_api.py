"""Formula routes with fake layers: every referenced company is checked before any query,
unknown and ungranted tickers look the same, and the response carries provenance."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app

from .conftest import SECRET
from .test_api import FakeAuthorizer, FakeStore

PEP, KO, NVDA = "0000077476", "0000021344", "0001045810"
TICKERS = {"PEP": PEP, "KO": KO, "NVDA": NVDA}
GRANTS = {"alice": {PEP: {"financials"}, NVDA: {"financials", "filings"}},
          "bob": {PEP: {"financials"}, KO: {"financials"}}}

ROW = {
    "period_key": 2025 * 4 + 1, "period_end": date(2025, 6, 14), "fiscal_key": True,
    "value": 1.8128, "inputs": [
        {"input": 0, "period_start": "2025-03-23", "period_end": "2025-06-14",
         "value": 22726000000, "value_source": "reported",
         "accession_no": "0000077476-25-000044", "form": "10-Q", "filed": "2025-07-17",
         "source_accessions": ["0000077476-25-000044"],
         "ingested_at": "2026-10-17T09:00:00+00:00", "filer_cik": "0000077476"},
        {"input": 1, "period_start": "2025-03-29", "period_end": "2025-06-27",
         "value": 12535000000, "value_source": "reported",
         "accession_no": "0000021344-25-000061", "form": "10-Q", "filed": "2025-07-24",
         "source_accessions": ["0000021344-25-000061", "0000021344-25-000029"],
         "ingested_at": "2026-10-17T09:00:00+00:00", "filer_cik": None}]}


class Store(FakeStore):
    def resolve_tickers(self, user_id, tickers):
        self.calls.append(("resolve_tickers", user_id, tuple(tickers)))
        return {t: TICKERS[t] for t in tickers if t in TICKERS}

    def tickers(self, user_id, company_ids):
        return {c: [t for t, i in TICKERS.items() if i == c] for c in company_ids}

    def formula(self, user_id, compiled):
        self.calls.append(("formula", user_id))
        self.compiled = compiled
        return [ROW]


@pytest.fixture
def client():
    store, authz = Store(), FakeAuthorizer(GRANTS)
    c = TestClient(create_app(authz, store, SECRET))
    c.store, c.authz = store, authz
    return c


def evaluate(client, token, user, **body):
    return client.post("/v1/formulas/evaluate", json=body, headers=token(user))


def test_requires_token(client):
    assert client.post("/v1/formulas/evaluate", json={"formula": "revenue"}).status_code == 401
    assert client.post("/v1/formulas/validate", json={"formula": "revenue"}).status_code == 401


def test_cross_company_formula_checks_every_company(client, token):
    r = evaluate(client, token, "bob", formula="PEP.revenue / KO.revenue")
    assert r.status_code == 200
    assert sorted(client.authz.checks) == [("bob", "financials", KO), ("bob", "financials", PEP)]
    body = r.json()
    assert (body["unit"], body["alignment"]) == ("ratio", "calendar")
    assert [i["ref"] for i in body["inputs"]] == ["PEP.revenue", "KO.revenue"]
    assert [i["company_id"] for i in body["inputs"]] == [PEP, KO]
    point = body["points"][0]
    assert (point["period"], point["period_end"], point["value"]) == (
        "CY2025 Q2", "2025-06-14", 1.8128)
    assert point["source_accessions"] == [
        "0000021344-25-000029", "0000021344-25-000061", "0000077476-25-000044"]
    cite = point["inputs"][0]["citation"]
    assert cite["filing_url"] == \
        "https://www.sec.gov/Archives/edgar/data/77476/000007747625000044/"
    assert point["inputs"][1]["freshness"]["filed"] == "2025-07-24"


def test_both_routes_say_how_a_dimensionless_result_reads(client, token):
    body = {"formula": "PEP.revenue / KO.revenue"}
    assert evaluate(client, token, "bob", **body).json()["display"] == "multiple"
    v = client.post("/v1/formulas/validate", json=body, headers=token("bob")).json()
    assert (v["valid"], v["unit"], v["display"]) == (True, "ratio", "multiple")
    body = {"formula": "receivables / revenue * 365", "default_company": PEP}
    assert client.post("/v1/formulas/validate", json=body,
                       headers=token("bob")).json()["display"] == "number"
    body = {"formula": "revenue", "default_company": PEP}
    assert client.post("/v1/formulas/validate", json=body,
                       headers=token("bob")).json()["display"] is None


def test_every_bad_name_comes_back_and_no_route_is_named(client, token):
    body = {"formula": "ebitda / revnue", "default_company": PEP}
    v = client.post("/v1/formulas/validate", json=body, headers=token("bob")).json()
    assert v["valid"] is False
    assert [e["message"] for e in v["errors"]] == [
        "Unknown metric 'ebitda'", "Unknown metric 'revnue'"]
    assert v["errors"][1]["hint"] == "Did you mean revenue?"
    assert "/v1/" not in str(v)
    r = evaluate(client, token, "bob", **body)
    assert r.status_code == 422 and len(r.json()["detail"]["errors"]) == 2


def test_a_literal_zero_divisor_is_refused_before_any_query(client, token):
    body = {"formula": "revenue / 0", "default_company": PEP}
    v = client.post("/v1/formulas/validate", json=body, headers=token("bob")).json()
    assert v["valid"] is False and v["errors"][0]["message"] == "Can't divide by zero"
    r = evaluate(client, token, "bob", **body)
    assert r.status_code == 422 and "formula" not in [c[0] for c in client.store.calls]


def test_ungranted_company_in_formula_is_refused_before_any_query(client, token):
    r = evaluate(client, token, "alice", formula="KO.revenue / PEP.revenue")
    assert r.status_code == 403
    [err] = r.json()["detail"]["errors"]
    assert (err["message"], err["pos"], err["length"]) == ("KO isn't one of your companies", 0, 2)
    assert "formula" not in [c[0] for c in client.store.calls]


def test_unknown_and_ungranted_tickers_look_the_same(client, token):
    ungranted = evaluate(client, token, "alice", formula="KO.revenue").json()
    unknown = evaluate(client, token, "alice", formula="KQ.revenue").json()
    assert ungranted["detail"]["errors"][0]["message"] == "KO isn't one of your companies"
    assert unknown["detail"]["errors"][0]["message"] == "KQ isn't one of your companies"
    assert unknown["detail"]["errors"][0]["hint"] == ungranted["detail"]["errors"][0]["hint"]


def test_ticker_suggestions_come_only_from_own_companies(client, token):
    err = evaluate(client, token, "alice", formula="PEPP.revenue").json()["detail"]["errors"][0]
    assert err["hint"] == "Did you mean PEP?"
    err = evaluate(client, token, "alice", formula="KOO.revenue").json()["detail"]["errors"][0]
    assert err["hint"] == "Only companies granted to you can be charted"  # never "KO"


def test_default_company_is_required_and_checked(client, token):
    r = evaluate(client, token, "alice", formula="net_income / revenue")
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["message"] == \
        "Metrics without a ticker need a default company"
    r = evaluate(client, token, "alice", formula="net_income / revenue", default_company=KO)
    assert r.status_code == 403 and "formula" not in [c[0] for c in client.store.calls]
    r = evaluate(client, token, "alice", formula="net_income / revenue", default_company=PEP)
    assert r.status_code == 200 and r.json()["alignment"] == "fiscal"
    assert r.json()["points"][0]["period"] == "FY2025 Q2"


def test_single_company_with_ticker_and_default_aligns_fiscal(client, token):
    r = evaluate(client, token, "alice", formula="PEP.revenue / revenue", default_company=PEP)
    assert r.json()["alignment"] == "fiscal"
    r = evaluate(client, token, "alice", formula="PEP.revenue / revenue", default_company=PEP,
                 basis="calendar")
    assert r.json()["alignment"] == "calendar"


def test_request_fields_reach_the_compiler(client, token):
    evaluate(client, token, "alice", formula="ttm(revenue) / revenue[-4q]",
             default_company=PEP, view="ttm", basis="calendar", **{"from": "2020-01-01"},
             to="2025-12-31")
    params = client.store.compiled.params
    assert params[:10] == [0, PEP, "revenue", "ttm", 0, 1, PEP, "revenue", "ttm", 4]
    assert params[-4:] == [date(2020, 1, 1)] * 2 + [date(2025, 12, 31)] * 2


@pytest.mark.parametrize("formula, code", [
    ("revenue + eps_diluted", "unit"),
    ("revnue", "unknown_metric"),
    ("revenue; DROP TABLE gold.financials", "syntax"),
    ("revenue[-50q]", "limit"),
    ("A.revenue + B.revenue + C.revenue + D.revenue + E.revenue + F.revenue", "limit"),
])
def test_bad_formulas_are_422_and_never_query(client, token, formula, code):
    r = evaluate(client, token, "bob", formula=formula, default_company=PEP)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["code"] == code
    assert client.store.calls == [] and client.authz.checks == []


def test_overlong_formula_is_rejected(client, token):
    assert evaluate(client, token, "bob", formula="x" * 3000).status_code == 422
    r = evaluate(client, token, "bob", formula="revenue+" * 70 + "revenue",
                 default_company=PEP)
    assert r.json()["detail"]["errors"][0]["code"] == "limit"


@pytest.mark.parametrize("body", [
    {"formula": "revenue", "default_company": "PEP"},
    {"formula": "revenue", "default_company": "0000077476' OR '1'='1"},
    {"formula": "revenue", "default_company": PEP, "basis": "lunar"},
    {"formula": "revenue", "default_company": PEP, "view": "annual"},
])
def test_rejects_malformed_fields(client, token, body):
    r = client.post("/v1/formulas/evaluate", json=body, headers=token("bob"))
    assert r.status_code == 422 and client.store.calls == []


def test_user_comes_from_token(client, token):
    client.post("/v1/formulas/evaluate?user_id=bob",
                json={"formula": "KO.revenue", "user_id": "bob"}, headers=token("alice"))
    assert {c[0] for c in client.authz.checks} == {"alice"}


def test_fails_closed_when_openfga_is_down(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), Store(), SECRET))
    r = c.post("/v1/formulas/evaluate", json={"formula": "PEP.revenue"}, headers=token("bob"))
    assert r.status_code == 503


def test_validate_ok(client, token):
    r = client.post("/v1/formulas/validate", headers=token("bob"),
                    json={"formula": "KO.net_income / KO.revenue - net_margin",
                          "default_company": PEP})
    body = r.json()
    assert body["valid"] and body["unit"] == "ratio" and body["alignment"] == "calendar"
    assert body["ast"]["type"] == "binary"
    assert [(i["ref"], i["company_id"]) for i in body["inputs"]] == [
        ("KO.net_income", KO), ("KO.revenue", KO), ("net_margin", PEP)]
    assert ("formula", "bob") not in client.store.calls


@pytest.mark.parametrize("formula, message, pos", [
    ("KO.revenue / PEP.revenue", "KO isn't one of your companies", 0),
    ("revenue + eps_diluted", "Can't add dollars and dollars per share", 8),
    ("revenue +", "Formula ends too early", 9),
])
def test_validate_errors(client, token, formula, message, pos):
    r = client.post("/v1/formulas/validate", headers=token("alice"),
                    json={"formula": formula, "default_company": PEP})
    assert r.status_code == 200
    body = r.json()
    assert not body["valid"]
    assert (body["errors"][0]["message"], body["errors"][0]["pos"]) == (message, pos)


def in_units(*units):
    """ROW with its inputs' units (057: the compiled query returns each row's own)."""
    return {**ROW, "inputs": [{**x, "unit": u} for x, u in zip(ROW["inputs"], units, strict=True)]}


def test_a_series_says_its_currency_and_two_currencies_dont_mix(client, token):
    """057: Alibaba's yuan revenue isn't "$996B": the answer says CNY. A dollar series says
    nothing (its JSON is as before). One company's own ratio has no currency; two companies'
    amounts in different currencies are refused, whatever the formula does with them."""
    client.store.formula = lambda user_id, compiled: [in_units("CNY", "CNY")]
    one = evaluate(client, token, "bob", formula="PEP.revenue").json()
    assert one["currency"] == "CNY" and one["unit"] == "USD"
    assert one["inputs"][0]["currency"] == "CNY"
    per_share = evaluate(client, token, "bob", formula="PEP.eps_diluted").json()
    assert per_share["currency"] == "CNY" and per_share["unit"] == "USD/shares"
    ratio = evaluate(client, token, "bob", formula="PEP.revenue / KO.revenue").json()
    assert "currency" not in ratio  # dimensionless
    client.store.formula = lambda user_id, compiled: [in_units("USD", "USD")]
    dollars = evaluate(client, token, "bob", formula="PEP.revenue").json()
    assert "currency" not in dollars and "currency" not in dollars["inputs"][0]
    client.store.formula = lambda user_id, compiled: [in_units("EUR", "USD")]
    for formula in ("PEP.revenue - KO.revenue", "PEP.revenue / KO.revenue"):
        r = evaluate(client, token, "bob", formula=formula)
        assert r.status_code == 422
        [e] = r.json()["detail"]["errors"]
        assert e["code"] == "currency"
        assert e["message"].startswith("These figures are in different currencies "
                                       "(PEP.revenue is in EUR; KO.revenue is in USD)")


def test_a_point_built_from_a_ratio_that_reads_nm_says_why(client, token):
    """F7a item 7: the chart's points carry the tables' n/m reading (migration 035) per input;
    value stays the exact figure, so an older web app draws what it always drew."""
    nm = {**ROW, "inputs": [{**ROW["inputs"][0],
                             "not_meaningful": "revenue is under $100K, too small a base"},
                            ROW["inputs"][1]]}
    client.store.formula = lambda user_id, compiled: [nm]
    one = evaluate(client, token, "bob", formula="PEP.revenue").json()["points"][0]
    assert one["value"] == 1.8128
    assert one["not_meaningful"] == "revenue is under $100K, too small a base"
    assert one["inputs"][0]["not_meaningful"] == one["not_meaningful"]
    assert "not_meaningful" not in one["inputs"][1]
    two = evaluate(client, token, "bob", formula="PEP.revenue / KO.revenue").json()["points"][0]
    assert two["not_meaningful"] == "PEP.revenue: revenue is under $100K, too small a base"
    client.store.formula = lambda user_id, compiled: [ROW]
    plain = evaluate(client, token, "bob", formula="PEP.revenue").json()["points"][0]
    assert "not_meaningful" not in plain
