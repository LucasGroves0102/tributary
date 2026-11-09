"""Reported vs standardised (F5, query_api.components): a figure opened into what gold made it of.
Rows are in Store.components' shape; NVIDIA's are production's gold of 2026-11-09 (FY2027 Q2)."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.components import fact_selection, how_built, shape

from .conftest import SECRET

NVDA = "0001045810"
END = date(2026, 7, 26)
START = date(2026, 4, 27)
ACC = "0001045810-26-000075"


def row(metric: str, value: str, tag: str = "", source: str = "reported", inputs: Any = None,
        start: date | None = START, acc: str = ACC, accs: list[str] | None = None) -> dict:
    return {"metric": metric, "period_start": start, "period_end": END, "fiscal_year": 2027,
            "fiscal_quarter": 2, "value": Decimal(value), "unit": "USD", "value_source": source,
            "source_tag": tag, "inputs": inputs, "accession_no": ACC if acc is None else acc,
            "form": "10-Q", "filed": date(2026, 8, 26), "source_accessions": accs or [acc],
            "filer_cik": NVDA}


def fact(tag: str, value: str, priority: int, start: date | None, acc: str = ACC,
         end: date = END, line: bool = True) -> dict:
    return {"tag": tag, "taxonomy": "us-gaap", "priority": priority, "gold_line": line,
            "period_start": start, "period_end": end, "value": Decimal(value), "unit": "USD",
            "accession_no": acc, "filer_cik": NVDA, "form": "10-Q", "filed": date(2026, 8, 26),
            "filings": 1}


BANK_SUM = "InterestIncomeExpenseNet+NoninterestIncome"
COMPUTED = "derived:revenue-cost_of_revenue"
URL = f"/v1/companies/{NVDA}/components?metric=net_debt&period_end="
NET_DEBT = row("net_debt", "-66003000000", source="computed", start=None, inputs={
    "long_term_debt": 8466000000, "long_term_debt_current": 0, "cash": 23422000000,
    "short_term_investments": 51047000000})


def test_how_a_figure_was_built():
    assert how_built(NET_DEBT) == "derived"
    assert how_built(row("revenue", "1", tag=f"us-gaap:{BANK_SUM}")) == "sum"
    assert how_built(row("gross_profit", "1", tag=COMPUTED, source="derived")) == "computed"
    assert how_built(row("revenue", "1", tag="us-gaap:Revenues", source="derived")) == "ytd"
    assert how_built(row("revenue", "1", inputs={"quarters": []}, source="ttm")) == "ttm"
    assert how_built(row("revenue", "1", tag="us-gaap:Revenues")) == "reported"


def test_net_debt_opens_into_debt_less_cash_and_investments_with_their_tags():
    cash_tag = "CashAndCashEquivalentsAtCarryingValue"
    rows = {"long_term_debt": row("long_term_debt", "8466000000", "us-gaap:LongTermDebtNoncurrent",
                                  start=None),
            "cash": row("cash", "23422000000", f"us-gaap:{cash_tag}", start=None),
            "short_term_investments": row(
                "short_term_investments", "51047000000",
                "us-gaap:DebtSecuritiesCurrent+EquitySecuritiesFvNi", start=None)}
    facts = {("long_term_debt", END): [fact("LongTermDebtNoncurrent", "8466000000", 1, None),
                                       fact("LongTermDebt", "8466000000", 3, None)],
             ("short_term_investments", END): [
                 fact("DebtSecuritiesCurrent", "34143000000", 10, None),
                 fact("EquitySecuritiesFvNi", "16904000000", 11, None, line=False)],
             ("cash", END): [fact(cash_tag, "23422000000", 1, None)]}
    out = shape(NVDA, "net_debt", "quarter", NET_DEBT, rows, facts, "debt - cash", set(rows))
    assert out["how"] == "derived" and out["figure"]["value"] == "-66003000000"
    parts = {c["metric"]: c for c in out["components"]}
    assert [c["metric"] for c in out["components"]] == [
        "long_term_debt", "long_term_debt_current", "cash", "short_term_investments"]
    assert [c["role"] for c in out["components"]] == ["+", "+", "-", "-"]
    debt = parts["long_term_debt"]
    assert debt["value"] == "8466000000"
    assert debt["figure"]["citation"]["xbrl_tag"] == "us-gaap:LongTermDebtNoncurrent"
    assert [(f["tag"], f["used"]) for f in debt["facts"]] == [
        ("LongTermDebtNoncurrent", True), ("LongTermDebt", False)]
    sti = parts["short_term_investments"]
    assert [(f["tag"], f["used"]) for f in sti["facts"]] == [
        ("DebtSecuritiesCurrent", True), ("EquitySecuritiesFvNi", True)]
    assert parts["long_term_debt_current"]["figure"] is None  # no gold row given: the input stands


def test_investments_counted_in_cash_say_so():
    inside = row("net_debt", "1", source="computed", start=None,
                 inputs={"long_term_debt": 10, "cash": 5})
    rows = {"short_term_investments": row("short_term_investments", "3", start=None)}
    out = shape(NVDA, "net_debt", "quarter", inside, rows, {}, None, {"short_term_investments"})
    sti = next(c for c in out["components"] if c["metric"] == "short_term_investments")
    assert sti == {"metric": "short_term_investments", "role": "-", "value": None, "used": False,
                   "note": "inside_cash"}
    none = shape(NVDA, "cash_and_investments", "quarter", inside, {}, {}, None, set())
    left = next(c for c in none["components"] if c["metric"] == "short_term_investments")
    assert left["note"] == "not_reported"


def test_a_derived_quarter_is_its_two_years_to_date():
    q4 = row("revenue", "39331000000", "us-gaap:Revenues", source="derived",
             start=date(2025, 10, 27), acc="0001045810-26-000021",
             accs=["0001045810-25-000230", "0001045810-26-000021"])
    year = fact("Revenues", "130497000000", 1, date(2025, 1, 27), acc="0001045810-26-000021")
    nine = fact("Revenues", "91166000000", 1, date(2025, 1, 27), acc="0001045810-25-000230",
                end=date(2025, 10, 26))
    quarter = fact("Revenues", "1", 1, date(2025, 10, 27), acc="x")
    out = fact_selection(q4, [year, quarter], [nine])
    assert [(f["value"], f["role"], f["accession_no"]) for f in out] == [
        ("130497000000", "+", "0001045810-26-000021"),
        ("91166000000", "-", "0001045810-25-000230")]


def test_a_sum_marks_both_parts_used():
    r = row("revenue", "100", tag=f"us-gaap:{BANK_SUM}")
    out = fact_selection(r, [fact("InterestIncomeExpenseNet", "60", 7, START, line=False),
                             fact("NoninterestIncome", "40", 8, START, line=False),
                             fact("Revenues", "130", 2, START),
                             fact("Revenues", "500", 2, date(2025, 10, 27))], [])
    assert [(f["tag"], f["used"]) for f in out] == [
        ("InterestIncomeExpenseNet", True), ("NoninterestIncome", True), ("Revenues", False)]


def test_a_computed_gross_profit_shows_revenue_less_cost():
    gp = row("gross_profit", "30", tag=COMPUTED, source="derived")
    gm = row("gross_margin", "0.3", source="computed", inputs={"gross_profit": 30, "revenue": 100})
    rows = {"gross_profit": gp, "revenue": row("revenue", "100", "us-gaap:Revenues"),
            "cost_of_revenue": row("cost_of_revenue", "70", "us-gaap:CostOfRevenue")}
    out = shape("0001674101", "gross_margin", "quarter", gm, rows, {}, "gross_profit / revenue",
                set(rows))
    gross = out["components"][0]
    assert gross["role"] == "numerator" and gross["facts"] == []
    assert [(p["metric"], p["role"], p["value"]) for p in gross["parts"]] == [
        ("revenue", "+", "100"), ("cost_of_revenue", "-", "70")]


def test_a_ttm_is_its_quarters():
    quarters = [{"period_start": "2025-07-28", "period_end": "2025-10-26", "value": 2,
                 "accession_no": "a"},
                {"period_start": "2025-10-27", "period_end": "2026-01-25", "value": 8,
                 "accession_no": "b"}]
    ttm = row("revenue", "10", source="ttm", inputs={"quarters": quarters})
    out = shape(NVDA, "revenue", "ttm", ttm, {}, {}, None, set())
    assert out["how"] == "ttm" and out["facts"] == []
    assert [p["value"] for p in out["parts"]] == ["2", "8"]


class ComponentsStore:
    def __init__(self, found: bool = True):
        self.calls: list = []
        self.found = found

    def metric_names(self):
        return {"metrics": ["cash", "long_term_debt"], "derived_metrics": ["net_debt"]}

    def components(self, user_id, company_id, span, metric, period_end):
        self.calls.append((user_id, company_id, span, metric, period_end))
        return {"row": NET_DEBT if self.found else None, "rows": {}, "facts": {},
                "definition": "debt - cash", "present": set()}


class Gate:
    def __init__(self, ok: bool):
        self.ok = ok

    def can(self, user_id, tier, company_id):
        assert tier == "financials"
        return self.ok


def client(ok: bool = True, found: bool = True) -> Any:
    store = ComponentsStore(found)
    c: Any = TestClient(create_app(Gate(ok), store, SECRET))  # type: ignore[arg-type]
    c.store = store
    return c


def test_route_requires_the_financials_tier_before_the_store(token):
    c = client(ok=False)
    r = c.get(URL + "2026-07-26", headers=token("alice"))
    assert r.status_code == 403 and c.store.calls == []


def test_route_answers_from_the_store(token):
    c = client()
    r = c.get(URL + "2026-07-26", headers=token("alice"))
    assert r.status_code == 200, r.text
    assert c.store.calls == [("alice", NVDA, "quarter", "net_debt", END)]
    body = r.json()
    assert body["how"] == "derived" and body["definition"] == "debt - cash"
    assert body["figure"]["citation"]["filing_url"].endswith("/000104581026000075/")


def test_route_refuses_unknown_metrics_and_missing_figures(token):
    c = client(found=False)
    bad = c.get(URL.replace("net_debt", "nope") + "2026-07-26", headers=token("alice"))
    assert bad.status_code == 422 and c.store.calls == []
    assert c.get(URL + "2020-01-01", headers=token("alice")).status_code == 404
    assert c.get(URL + "2026-07-26").status_code == 401
