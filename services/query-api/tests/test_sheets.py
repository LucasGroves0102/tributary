"""POST /v1/sheets/values (query_api.sheets): many figures for a spreadsheet in one request. Who is
checked and how often, what a company outside the caller's access reads as, the readings a cell
shows instead of a number, periods, as_of, per-share figures and the limits. Store is a fake; the
SQL runs against Postgres in test_sheets_db.py."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.sheets import MAX_COMPANIES, MAX_REQUESTS, Period, label, parse_period

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer
from .test_api_keys import FakeKeys, bearer, make_key

EURO, BANK, SPLIT = "0000937966", "0000019617", "0000910521"  # ASML, JPMorgan, Deckers
TICKERS = {"NVDA": NVDA, "1045810": NVDA, "TSLA": TSLA, "ASML": EURO, "JPM": BANK,
           "DECK": SPLIT}
GRANTS = {"alice": {NVDA: {"financials"}, EURO: {"financials"}, BANK: {"financials"},
                    SPLIT: {"financials"}},
          "bob": {TSLA: {"financials"}}}
TODAY = date(2026, 11, 17)
PER_SHARE = json.loads((Path(__file__).parent / "fixtures" / "per_share" / "nvda.json")
                       .read_text())


def row(metric, end, fy, fq, value, *, cy=None, cq=None, unit="USD", nm=None, acc=None,
        cik=NVDA):
    return {"metric": metric, "period_start": None, "period_end": date.fromisoformat(end),
            "fiscal_year": fy, "fiscal_quarter": fq, "calendar_year": cy or fy - 1,
            "calendar_quarter": cq or fq, "value": Decimal(value), "unit": unit,
            "value_source": "reported", "source_tag": "us-gaap:Revenues", "inputs": None,
            "accession_no": acc or f"{cik}-26-0000{fq}{fy % 10}", "form": "10-Q",
            "filed": date.fromisoformat(end), "source_accessions": [],
            "ingested_at": "2026-10-17T09:56:49Z", "not_meaningful": nm, "filer_cik": cik}


ROWS = {
    (NVDA, "quarter", "fiscal"): [
        row("revenue", "2026-07-26", 2027, 2, "46743000000", acc="0001045810-26-000075"),
        row("revenue", "2026-04-26", 2027, 1, "44062000000"),
        row("revenue", "2026-01-25", 2026, 4, "39331000000"),
        row("revenue", "2025-10-26", 2026, 3, "35082000000"),
        row("gross_margin", "2026-07-26", 2027, 2, "0.7215", unit="pure"),
        row("revenue_growth", "2026-07-26", 2027, 2, "11.2", unit="pure",
            nm="growth of over 1,000% from under $10M earlier"),
    ],
    (NVDA, "quarter", "calendar"): [
        row("revenue", "2026-07-26", 2027, 2, "46743000000", cy=2026, cq=2),
        row("revenue", "2026-04-26", 2027, 1, "44062000000", cy=2026, cq=1),
        row("gross_margin", "2026-07-26", 2027, 2, "0.7215", cy=2026, cq=2, unit="pure"),
    ],
    (NVDA, "ttm", "fiscal"): [
        row("revenue", "2026-07-26", 2027, 2, "165218000000"),
        row("revenue", "2026-04-26", 2027, 1, "148513000000"),
        row("revenue", "2026-01-25", 2026, 4, "130497000000", acc="0001045810-26-000021"),
        row("revenue", "2025-01-26", 2025, 4, "60922000000"),
    ],
    (EURO, "quarter", "fiscal"): [
        row("revenue", "2026-09-28", 2026, 3, "7516000000", unit="EUR", cik=EURO)],
    (EURO, "ttm", "fiscal"): [
        row("revenue", "2025-12-31", 2025, 4, "28263000000", unit="EUR", cik=EURO)],
}
NVDA_LATEST = {"period_end": date(2026, 7, 26), "value": Decimal("46743000000"),
               "accession_no": "0001045810-26-000075", "form": "10-Q",
               "filed": date(2026, 8, 26), "status": None, "reason": None, "unit": "USD",
               "period_status": None, "filer_cik": "0001045810"}
READINGS = {
    (NVDA, "quarter", "revenue"): NVDA_LATEST,
    (NVDA, "ttm", "revenue"): {**NVDA_LATEST, "value": Decimal("165218000000")},
    (NVDA, "quarter", "gross_margin"): {**NVDA_LATEST, "value": Decimal("0.7215"),
                                        "unit": "pure"},
    (NVDA, "quarter", "net_margin"): {**NVDA_LATEST, "value": Decimal("0.5"), "unit": "pure",
                                      "status": "stale", "reason": "this company's latest "
                                      "figures here are for the period ended Jul 26, 2026"},
    (NVDA, "quarter", "roe"): {**NVDA_LATEST, "value": Decimal("-3.1"), "unit": "pure",
                               "status": "not_meaningful",
                               "reason": "stockholders' equity is negative"},
    (NVDA, "quarter", "cash"): {**NVDA_LATEST, "period_end": date(2019, 1, 27),
                                "value": Decimal("1"), "status": "behind",
                                "period_status": "behind"},
    (NVDA, "quarter", "net_debt"): {**NVDA_LATEST, "period_end": date(2014, 1, 26),
                                    "value": Decimal("2"), "status": "old",
                                    "period_status": "old"},
    (NVDA, "quarter", "eps_diluted"): {**NVDA_LATEST, "value": Decimal("2.46"),
                                       "unit": "USD/shares"},
    (EURO, "quarter", "revenue"): {**NVDA_LATEST, "period_end": date(2026, 9, 28),
                                   "value": Decimal("7516000000"), "unit": "EUR",
                                   "status": "other_currency", "reason": "reported in EUR",
                                   "filer_cik": EURO},
    (EURO, "ttm", "revenue"): {**NVDA_LATEST, "period_end": date(2025, 12, 31),
                               "value": Decimal("28263000000"), "unit": "EUR",
                               "status": "other_currency", "reason": "reported in EUR",
                               "period_status": "behind", "filer_cik": EURO},
    (BANK, "quarter", "gross_margin"): {**NVDA_LATEST, "value": None, "unit": None,
                                        "period_end": None, "accession_no": None,
                                        "status": "not_applicable",
                                        "reason": "this is a bank: deposits are its debt"},
    (BANK, "ttm", "gross_margin"): {**NVDA_LATEST, "value": None, "unit": None,
                                    "period_end": None, "accession_no": None,
                                    "status": "not_applicable",
                                    "reason": "this is a bank: deposits are its debt"},
}


class SheetStore:
    def __init__(self):
        self.calls: list[tuple] = []

    def metric_names(self):
        return {"metrics": ["cash", "eps_diluted", "revenue", "weighted_shares_diluted"],
                "derived_metrics": ["gross_margin", "net_debt", "net_margin", "revenue_growth",
                                    "roe"]}

    def resolve_companies(self, user_id, names):
        self.calls.append(("resolve_companies", user_id, tuple(names)))
        return {n: TICKERS[n] for n in names if n in TICKERS}

    def financials(self, user_id, company_id, metrics, quarters, view, basis, as_of=None):
        self.calls.append(("financials", user_id, company_id, tuple(metrics), quarters, view,
                           basis, as_of))
        return [r for r in ROWS.get((company_id, view, basis), []) if r["metric"] in metrics]

    def per_share_inputs(self, user_id, company_id, as_of=None):
        self.calls.append(("per_share_inputs", user_id, company_id, as_of))
        return PER_SHARE if company_id == NVDA else {"quarters": [], "annual": [],
                                                     "findings": []}

    def sheet_readings(self, user_id, company_id, pairs, dates, as_of=None):
        self.calls.append(("sheet_readings", user_id, company_id, tuple(pairs), tuple(dates),
                           as_of))
        return {"latest": {(s, m): READINGS[(company_id, s, m)] for s, m in pairs
                           if (company_id, s, m) in READINGS},
                "ages": {d: "behind" if d < date(2026, 3, 1) else None for d in dates}}

    def not_computed(self, user_id, company_id, metrics):
        self.calls.append(("not_computed", user_id, company_id, tuple(metrics)))
        return {"gross_margin": "Tributary computes no gross margin for a warehouse club"} \
            if "gross_margin" in metrics else {}

    def reads(self):
        return [c for c in self.calls if c[0] != "resolve_companies"]


@pytest.fixture
def client():
    store, authz = SheetStore(), FakeAuthorizer(GRANTS)
    c = TestClient(create_app(authz, store, SECRET, today=lambda: TODAY))
    c.store, c.authz = store, authz
    return c


def values(client, token, asks, user="alice"):
    r = client.post("/v1/sheets/values", json={"requests": asks}, headers=token(user))
    assert r.status_code == 200, r.text
    return r.json()["values"]


def one(client, token, user="alice", **ask):
    return values(client, token, [ask], user)[0]


# --- periods ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "want"), [
    (None, Period("quarter", "latest")), ("", Period("quarter", "latest")),
    ("latest", Period("quarter", "latest")), ("TTM", Period("ttm", "latest")),
    ("ttm latest", Period("ttm", "latest")), ("FY", Period("year", "latest")),
    ("fy2024", Period("year", "fiscal", 2024)), ("FY 2024", Period("year", "fiscal", 2024)),
    ("FY2024 Q3", Period("quarter", "fiscal", 2024, 3)),
    ("fy2024q3", Period("quarter", "fiscal", 2024, 3)),
    ("TTM FY2024 Q3", Period("ttm", "fiscal", 2024, 3)),
    ("CY2024 Q3", Period("quarter", "calendar", 2024, 3)),
    ("ttm cy2024 q1", Period("ttm", "calendar", 2024, 1)),
    ("2024-09-29", Period("quarter", "day", day=date(2024, 9, 29))),
    ("TTM 2024-09-29", Period("ttm", "day", day=date(2024, 9, 29))),
])
def test_period_forms(text, want):
    assert parse_period(text) == want


@pytest.mark.parametrize("text", ["Q3", "FY24", "FY2024 Q5", "CY2024", "2024-13-01", "last",
                                  "FYQ3", "2024", "TTM TTM"])
def test_period_forms_refused_with_words(text):
    with pytest.raises(ValueError, match="period"):
        parse_period(text)


def test_labels_read_back_as_the_same_period():
    r = row("revenue", "2026-07-26", 2027, 2, "1", cy=2026, cq=2)
    for text in ("FY2027 Q2", "TTM FY2027 Q2", "CY2026 Q2", "TTM CY2026 Q2", "FY2027"):
        p = parse_period(text)
        assert label(p, {**r, "fiscal_quarter": 4} if p.span == "year" else r) == text


# --- access -----------------------------------------------------------------------------------

def test_needs_a_sign_in_or_key(client):
    assert client.post("/v1/sheets/values", json={"requests": [
        {"company": "NVDA", "metric": "revenue"}]}).status_code == 401


def test_one_check_per_company_however_many_cells(client, token):
    asks = [{"company": c, "metric": m, "period": p}
            for c in ("NVDA", "1045810", "nvda") for m in ("revenue", "gross_margin")
            for p in ("latest", "FY2027 Q2", "TTM" if m == "revenue" else "CY2026 Q2")]
    got = values(client, token, asks)
    assert all(v["status"] in ("current", "ok") for v in got), got
    assert client.authz.checks == [("alice", "financials", NVDA)]
    # one company read once: one call per span and basis, one readings query
    reads = [c[0] for c in client.store.reads()]
    assert reads.count("sheet_readings") == 1 and reads.count("financials") == 3


def test_unknown_and_unreadable_companies_read_the_same_and_nothing_is_read(client, token):
    got = values(client, token, [{"company": "TSLA", "metric": "revenue"},
                                 {"company": "ZZZZ", "metric": "revenue"},
                                 {"company": "0001318605", "metric": "revenue", "period": "FY"}])
    strip = [{k: v for k, v in g.items() if k != "company"} for g in got]
    assert strip[0] == strip[1] == strip[2] == {
        **{k: None for k in strip[0]}, "status": "not_permitted",
        "reason": "not permitted to read financials for this company"}
    assert client.store.reads() == []  # no figure of an unreadable company is ever asked for
    assert client.authz.checks == [("alice", "financials", TSLA)]  # unknown: nothing to check


def test_the_user_is_the_tokens_never_the_body(client, token):
    r = client.post("/v1/sheets/values", headers=token("bob"),
                    json={"requests": [{"company": "NVDA", "metric": "revenue"}],
                          "user_id": "alice"})
    assert r.status_code == 422  # no such field
    got = one(client, token, user="bob", company="NVDA", metric="revenue")
    assert got["status"] == "not_permitted"
    assert ("bob", "financials", NVDA) in client.authz.checks


def test_openfga_down_fails_the_whole_request_closed(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), SheetStore(), SECRET))
    r = c.post("/v1/sheets/values", headers=token("alice"),
               json={"requests": [{"company": "NVDA", "metric": "revenue"}]})
    assert r.status_code == 503


def test_an_api_key_reaches_it_and_the_whole_sheet_counts_once(token):
    keys = FakeKeys()
    store = SheetStore()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET, api_keys=keys,
                              today=lambda: TODAY))
    key = make_key(c, token)["key"]
    asks = [{"company": "NVDA", "metric": "revenue", "period": f"FY2027 Q{q}"} for q in (1, 2)]
    r = c.post("/v1/sheets/values", json={"requests": asks * 50}, headers=bearer(key))
    assert r.status_code == 200 and len(r.json()["values"]) == 100
    assert r.headers["X-RateLimit-Remaining-Minute"] == "59"
    assert sum(u["requests"] for u in keys.usage.values()) == 1


# --- limits -----------------------------------------------------------------------------------

def test_at_most_200_asks_and_25_companies(client, token):
    ask = {"company": "NVDA", "metric": "revenue"}
    r = client.post("/v1/sheets/values", json={"requests": [ask] * (MAX_REQUESTS + 1)},
                    headers=token("alice"))
    assert r.status_code == 422
    many = [{"company": f"T{i}", "metric": "revenue"} for i in range(MAX_COMPANIES + 1)]
    r = client.post("/v1/sheets/values", json={"requests": many}, headers=token("alice"))
    assert r.status_code == 422 and "25 companies" in r.json()["detail"]
    assert client.store.calls == [] and client.authz.checks == []
    # a day per company counts too: the same company as of 26 days
    days = [{**ask, "as_of": f"2024-01-{d:02d}"} for d in range(1, MAX_COMPANIES + 2)]
    assert client.post("/v1/sheets/values", json={"requests": days},
                       headers=token("alice")).status_code == 422


@pytest.mark.parametrize(("ask", "words"), [
    ({"company": "NVDA", "metric": "revnue"}, "did you mean revenue"),
    ({"company": "NVDA", "metric": "revenue", "period": "Q3"}, "period: one of"),
    ({"company": "NVDA", "metric": "revenue", "as_of": "2030-01-01"}, "as_of"),
    ({"company": "NVDA", "metric": "revenue", "as_of": "yesterday"}, "YYYY-MM-DD"),
    ({"company": "NVDA", "metric": "revenue", "currency": "euro"}, "three-letter"),
    ({"company": "NVDA", "metric": "eps_diluted", "shares": "split"}, "adjusted or as_filed"),
    ({"company": "NVDA", "metric": "revenue", "count": 41}, "1 to 40"),
    ({"company": "NVDA", "metric": "revenue", "count": 4, "period": "FY2024"}, "series"),
    ({"company": "NV DA'; --", "metric": "revenue"}, "ticker"),
    ({"company": "X" * 150, "metric": "revenue"}, "ticker"),
    ({"company": "NVDA", "metric": "r" * 150}, "unknown metric"),
])
def test_a_bad_cell_is_its_own_answer_not_the_sheets(client, token, ask, words):
    got = values(client, token, [ask, {"company": "NVDA", "metric": "revenue"}])
    assert got[0]["status"] == "invalid" and words in got[0]["reason"]
    assert got[0]["value"] is None
    assert got[1]["status"] == "current"


# --- readings ---------------------------------------------------------------------------------

def test_latest_is_a_number_with_its_period_and_citation(client, token):
    got = one(client, token, company="NVDA", metric="revenue")
    assert got == {
        "company": "NVDA", "company_id": NVDA, "metric": "revenue", "span": "quarter",
        "status": "current", "reason": None, "value": "46743000000", "period": "FY2027 Q2",
        "period_end": "2026-07-26", "unit": "USD", "currency": "USD",
        "citation": {"accession_no": "0001045810-26-000075", "form": "10-Q",
                     "filed": "2026-08-26", "filing_url": "https://www.sec.gov/Archives/edgar/"
                     "data/1045810/000104581026000075/"}}


@pytest.mark.parametrize(("metric", "status", "words"), [
    ("net_margin", "stale", "latest figures here are for the period ended"),
    ("roe", "not_meaningful", "equity is negative"),
    ("net_debt", "old", "not reported since the period ended Jan 26, 2014"),
])
def test_a_reading_that_isnt_a_current_figure_says_why_instead(client, token, metric, status,
                                                               words):
    got = one(client, token, company="NVDA", metric=metric)
    assert (got["status"], got["value"]) == (status, None)
    assert words in got["reason"]
    assert got["citation"]["accession_no"]  # where it would have come from, still cited


def test_behind_is_still_a_number(client, token):
    got = one(client, token, company="NVDA", metric="cash")
    assert (got["status"], got["value"], got["period_end"]) == ("behind", "1", "2019-01-27")


def test_a_bank_metric_is_not_applicable_in_every_form(client, token):
    got = values(client, token, [{"company": "JPM", "metric": "gross_margin", "period": p}
                                 for p in ("latest", "TTM", "FY2024", "FY2024 Q3")]
                 + [{"company": "JPM", "metric": "gross_margin", "count": 4}])
    assert {g["status"] for g in got} == {"not_applicable"}
    assert all("bank" in g["reason"] and g["value"] is None for g in got)


def test_another_currency_only_when_asked_for_it(client, token):
    got = values(client, token, [
        {"company": "ASML", "metric": "revenue"},
        {"company": "ASML", "metric": "revenue", "currency": "eur"},
        {"company": "ASML", "metric": "revenue", "period": "TTM", "currency": "EUR"},
        {"company": "ASML", "metric": "revenue", "period": "FY2025"},
        {"company": "ASML", "metric": "revenue", "period": "FY2025", "currency": "EUR"},
        {"company": "NVDA", "metric": "revenue", "currency": "EUR"},
        {"company": "NVDA", "metric": "gross_margin", "currency": "EUR"},  # a ratio: none
    ])
    assert [(g["status"], g["value"]) for g in got] == [
        ("other_currency", None), ("current", "7516000000"), ("behind", "28263000000"),
        ("other_currency", None), ("ok", "28263000000"), ("other_currency", None),
        ("current", "0.7215")]
    assert "reported in EUR" in got[0]["reason"] and "doesn't convert" in got[0]["reason"]
    assert "reported in USD" in got[5]["reason"]
    assert got[1]["currency"] == "EUR"


# --- named periods and series -----------------------------------------------------------------

def test_named_periods(client, token):
    got = values(client, token, [
        {"company": "NVDA", "metric": "revenue", "period": "FY2027 Q1"},
        {"company": "NVDA", "metric": "revenue", "period": "CY2026 Q1"},
        {"company": "NVDA", "metric": "revenue", "period": "2026-01-25"},
        {"company": "NVDA", "metric": "revenue", "period": "TTM FY2027 Q1"},
        {"company": "NVDA", "metric": "revenue", "period": "FY2026"},
        {"company": "NVDA", "metric": "revenue", "period": "FY"},
        {"company": "NVDA", "metric": "revenue", "period": "FY2019 Q3"},
    ])
    assert [(g["status"], g["value"], g["period"]) for g in got] == [
        ("ok", "44062000000", "FY2027 Q1"), ("ok", "44062000000", "CY2026 Q1"),
        ("ok", "39331000000", "FY2026 Q4"), ("ok", "148513000000", "TTM FY2027 Q1"),
        ("ok", "130497000000", "FY2026"), ("behind", "130497000000", "FY2026"),
        ("no_figure", None, None)]
    assert got[4]["citation"]["accession_no"] == "0001045810-26-000021"
    # a named period reads every quarter on file; latest only the newest
    depths = {(c[5], c[6]): c[4] for c in client.store.calls if c[0] == "financials"}
    assert depths == {("quarter", "fiscal"): 120, ("quarter", "calendar"): 120,
                      ("ttm", "fiscal"): 120}


def test_a_named_period_reads_its_own_n_m_and_is_never_stale(client, token):
    got = values(client, token, [
        {"company": "NVDA", "metric": "revenue_growth", "period": "FY2027 Q2"},
        {"company": "NVDA", "metric": "net_margin", "period": "FY2027 Q2"},
        {"company": "NVDA", "metric": "gross_margin", "period": "FY2019 Q2"},
    ])
    assert (got[0]["status"], got[0]["value"]) == ("not_meaningful", None)
    assert "1,000%" in got[0]["reason"]
    assert got[1]["status"] == "no_figure"  # nothing on file then; stale never applies
    assert (got[2]["status"], got[2]["reason"]) == (
        "not_computed", "Tributary computes no gross margin for a warehouse club")


def test_a_series_is_newest_first_with_each_points_citation(client, token):
    got = one(client, token, company="NVDA", metric="revenue", count=3)
    assert got["status"] == "ok" and got["value"] is None
    assert [(p["period"], p["value"], p["status"]) for p in got["points"]] == [
        ("FY2027 Q2", "46743000000", "ok"), ("FY2027 Q1", "44062000000", "ok"),
        ("FY2026 Q4", "39331000000", "ok")]
    assert all(p["citation"]["filing_url"].startswith("https://www.sec.gov/")
               for p in got["points"])
    years = one(client, token, company="NVDA", metric="revenue", period="FY", count=2)
    assert [p["period"] for p in years["points"]] == ["FY2026", "FY2025"]
    depth = {(c[5], c[6]): c[4] for c in client.store.calls if c[0] == "financials"}
    assert depth == {("quarter", "fiscal"): 3, ("ttm", "fiscal"): 11}


# --- per share and as of ----------------------------------------------------------------------

def test_eps_comes_from_the_per_share_rules_split_adjusted_unless_asked(client, token):
    got = values(client, token, [
        {"company": "NVDA", "metric": "eps_diluted"},
        {"company": "NVDA", "metric": "eps_diluted", "period": "FY2026 Q4"},
        {"company": "NVDA", "metric": "eps_diluted", "period": "FY2019"},
        {"company": "NVDA", "metric": "eps_diluted", "period": "FY2019", "shares": "as_filed"},
        {"company": "NVDA", "metric": "eps_diluted", "period": "TTM"},
        {"company": "NVDA", "metric": "eps_diluted", "period": "FY"},
    ])
    assert [(g["status"], g["value"], g["period"]) for g in got] == [
        ("current", "2.46", "FY2027 Q2"),
        ("ok", "1.7585", "FY2026 Q4"),   # the derived fiscal Q4
        ("ok", "0.1658", "FY2019"),      # the 10-K's $6.63, on today's share basis
        ("ok", "6.63", "FY2019"),
        ("current", "7.9085", "TTM FY2027 Q2"),
        ("behind", "4.9", "FY2026")]     # the 10-K's own year, not the quarters' sum
    assert got[0]["unit"] == "USD/shares" and got[0]["citation"]["filing_url"]
    # gold's own rows are never read for per-share figures
    assert not [c for c in client.store.calls if c[0] == "financials"]


def test_as_of_reads_that_day_for_every_read_and_says_so(client, token):
    got = values(client, token, [
        {"company": "NVDA", "metric": "revenue", "as_of": "2024-06-30"},
        {"company": "NVDA", "metric": "eps_diluted", "as_of": "2024-06-30"},
        {"company": "NVDA", "metric": "revenue"},
    ])
    assert got[0]["known_on"] == got[1]["known_on"] == "2024-06-30"
    assert "known_on" not in got[2]
    day = date(2024, 6, 30)
    as_of = {(c[0], c[-1]) for c in client.store.reads()}
    assert as_of == {("financials", day), ("per_share_inputs", day), ("sheet_readings", day),
                     ("financials", None), ("sheet_readings", None)}
    assert client.authz.checks == [("alice", "financials", NVDA)]  # one company, two days
