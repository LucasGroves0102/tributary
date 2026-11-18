"""query_api/model_maths.py, the one copy of a model's maths (F2). Fixtures: B2 and B7's shared
cases (fixtures/model/b2-b7-cases.json, the numbers the web app and agent-api were held to before
F2 moved the maths here: history statistics, flags, run rate, defaults) and the real inputs of
NVIDIA, Deckers and Vertiv (fixtures/model/{nvda,deck,vrt}.json: production Query API answers and
gold.latest_reading as of 2026-11-09, in Store.model_inputs' shape)."""

import json
from pathlib import Path

import pytest

from query_api.model_maths import run, shifted
from query_api.workspaces import validate_state

FIXTURES = Path(__file__).parent / "fixtures" / "model"
B2B7 = json.loads((FIXTURES / "b2-b7-cases.json").read_text())
NVDA, DECK, VRT = "0001045810", "0000910521", "0001674101"


def real(ticker: str) -> dict:
    return json.loads((FIXTURES / f"{ticker}.json").read_text())


def model(company_id: str, years: list[dict], **extra) -> dict:
    return validate_state("model", {"companyId": company_id, "assumptions": years, **extra})


def years(n: int, growth: float = 10, margin: float = 20) -> list[dict]:
    return [{"growth": growth, "margin": margin}] * n


# --- B2 and B7: the same numbers as before F2 ------------------------------------------------

@pytest.mark.parametrize("case", B2B7["cases"], ids=lambda c: c["name"])
def test_history_statistics_and_flags_are_b2s(case):
    out = run({"ttm": case["rows"]}, model(NVDA, case["assumptions"]))
    want = case["expected"]
    assert [(y["fy"], y["period_end"], y["revenue"], y["operating_income"])
            for y in out["history"]] == [(h["fy"], h["periodEnd"], h["revenue"],
                                          h["operatingIncome"]) for h in want["history"]]
    got = out["cases"]["base"]["check"]
    assert got["base_fy"] == want["base_fy"]
    assert got["stats"] == pytest.approx(want["stats"])
    assert (got["margin_min"], got["margin_max"]) == pytest.approx(
        (want["margin_min"], want["margin_max"]))
    assert got["years"] == [pytest.approx(y) for y in want["years"]]


@pytest.mark.parametrize("case", B2B7["default_cases"], ids=lambda c: c["name"])
def test_defaults_are_b7s_run_rate_fading_to_the_long_run(case):
    want = case["expected"]
    out = run({"ttm": case["rows"]}, model(NVDA, years(case["years"])))
    rr = out["run_rate"]
    assert (rr["growth"], rr["margin"], rr["period"]) == (
        pytest.approx(want["run_rate"]["growth"]) if want["run_rate"]["growth"] is not None
        else None,
        pytest.approx(want["run_rate"]["margin"]) if want["run_rate"]["margin"] is not None
        else None, want["run_rate"]["period"])
    defaults = out["cases"]["base"]["defaults"]
    assert [{k: d[k] for k in ("growth", "margin")} for d in defaults] == want["defaults"]
    # a model still at a default (new or the pre-B7 flat one) isn't the user's view
    edited = run({"ttm": case["rows"]}, model(NVDA, want["edited"]))
    assert edited["cases"]["base"]["user_set"] == want["user_set"]


def test_each_cases_fcf_margin_default_follows_its_own_operating_margin():
    """morgan4 finding 15: bear, base and bull all read 31.7%, 16.2%, 12.4%, so Morgan's bull
    (margins 24, 25, 25.5%) had free cash flow falling $3.70B, $3.37B, $3.14B while its operating
    income rose to $6.47B. A case's default is the base case's FCF margin moved by its margin
    difference after tax (VRT's TTM rate, 19.8%). Since Morgan 16 the base case fades to the
    5-year median FCF margin (31.7%, 17.6%, 14.4%; the mean gave 16.2%, 12.4%)."""
    inputs = real("vrt")
    base_defs = run(inputs, model(VRT, years(3), ytd=True))["cases"]["base"]["defaults"]
    base = [{"growth": d["growth"], "margin": d["margin"]} for d in base_defs]
    bull = [{"growth": 60, "margin": 24}, {"growth": 40, "margin": 25},
            {"growth": 22, "margin": 25.5}]
    out = run(inputs, model(VRT, base, ytd=True, scenarios={"bear": shifted(base, "bear"),
                                                            "bull": bull}))
    c = out["cases"]
    assert [d["fcfMargin"] for d in c["base"]["defaults"]] == [31.7, 17.6, 14.4]  # unchanged
    assert [d["fcfMargin"] for d in c["bull"]["defaults"]] == [34.8, 24.5, 23.1]
    fcf = {k: [y["fcf"] for y in c[k]["years"]] for k in ("bear", "base", "bull")}
    oi = [y["operating_income"] for y in c["bull"]["years"]]
    assert oi[0] < oi[1] < oi[2] and fcf["bull"][0] < fcf["bull"][1] < fcf["bull"][2]
    assert all(b < m < u for b, m, u in zip(fcf["bear"], fcf["base"], fcf["bull"], strict=True))
    tax = c["bull"]["defaults"][1]["taxRate"]
    assert c["bull"]["defaults"][1]["fcfMargin"] == pytest.approx(
        17.6 + (25 - base[1]["margin"]) * (1 - tax / 100), abs=0.05)
    # the preview cases move by the spread alone; a rate the case typed is the case's
    preview = run(inputs, model(VRT, base, ytd=True))["cases"]
    assert [d["fcfMargin"] for d in preview["bear"]["defaults"]] == [27.7, 14.3, 11.5]
    assert [d["fcfMargin"] for d in preview["bull"]["defaults"]] == [35.7, 20.9, 17.4]
    typed = run(inputs, model(VRT, base, ytd=True, scenarios={
        "bull": [{**y, "fcfMargin": 5} for y in bull]}))["cases"]["bull"]["years"]
    assert [y["inputs"]["fcfMargin"] for y in typed] == [5.0, 5.0, 5.0]
    # the base case's own typed FCF margin is where the cases start; a 100% tax keeps nothing
    own = [{**y, "fcfMargin": 10} for y in base]
    cases = run(inputs, model(VRT, own, ytd=True, scenarios={
        "bull": [{**y, "taxRate": 100} for y in bull]}))["cases"]
    assert [d["fcfMargin"] for d in cases["bull"]["defaults"]] == [10.0, 10.0, 10.0]


def test_bear_and_bull_start_from_the_base_case_by_b7s_spread():
    """Growth by half (at least 5 points), the margin by a quarter of itself (Riley 35)."""
    base = [{"growth": 83, "margin": 60, "taxRate": 18}, {"growth": 2, "margin": 10}]
    assert shifted(base, "bear") == [{"growth": 41.5, "margin": 45.0, "taxRate": 18},
                                     {"growth": -3.0, "margin": 7.5}]
    assert shifted(base, "bull") == [{"growth": 124.5, "margin": 75.0, "taxRate": 18},
                                     {"growth": 7.0, "margin": 12.5}]
    # Costco's 3.9%: the old floor of 3 points read 0.9% and 6.9%; a loss stays proportional
    thin = [{"growth": 6, "margin": 3.9}, {"growth": 5, "margin": -8}, {"growth": 5, "margin": 0}]
    assert [y["margin"] for y in shifted(thin, "bear")] == [2.9, -10.0, 0.0]
    assert [y["margin"] for y in shifted(thin, "bull")] == [4.9, -6.0, 0.0]


# --- current year from actuals ----------------------------------------------------------------

def test_vertiv_fy2026_is_nine_months_reported_plus_q4():
    """Tester 1: FY26 as nine months actual plus Q4. The remainder's defaults: Q4 FY2025's
    revenue grown at the TTM's growth, at the TTM's operating margin (owner, 2026-11-09)."""
    out = run(real("vrt"), model(VRT, [{"growth": 50, "margin": 25}], ytd=True))
    cur = out["current"]
    assert (out["base_fy"], cur["fy"], cur["quarters"], cur["through"]) == (
        2025, 2026, 3, "2026-09-30")
    assert cur["actual"]["revenue"] == pytest.approx(2650e6 + 3274e6 + 4313.2e6, rel=1e-3)
    # the same nine months a year earlier, and the quarter left
    assert cur["actual"]["growth"] == pytest.approx(0.393, abs=1e-3)
    assert cur["remainder_prior"]["revenue"] == pytest.approx(2880e6, rel=1e-3)
    d = out["cases"]["base"]["defaults"][0]
    rr = out["run_rate"]
    assert rr["period"] == "FY2026 Q3"
    assert (d["growth"], d["margin"]) == (35.3, 20.1)
    assert (d["growth"], d["margin"]) == (round(rr["growth"] * 100, 1),
                                          round(rr["margin"] * 100, 1))
    # the year to date's 39.3% and last Q4's margin moved by this year's change aren't it
    assert cur["actual"]["growth"] * 100 == pytest.approx(39.3, abs=0.05)
    y = out["cases"]["base"]["years"][0]
    assert y["actual"]["quarters"] == 3 and y["remainder"]["quarters"] == 1
    q4 = cur["remainder_prior"]["revenue"] * 1.5
    assert y["remainder"]["revenue"] == pytest.approx(q4)
    assert y["revenue"] == pytest.approx(cur["actual"]["revenue"] + q4)
    assert y["operating_income_adjusted"] == pytest.approx(
        cur["actual"]["operating_income"] + q4 * 0.25)
    # the year is checked on its full year, not on the remainder's numbers
    check = out["cases"]["base"]["check"]["years"][0]
    assert check["growth"] == pytest.approx(y["growth"] * 100)
    assert check["growth"] != 50


def test_deckers_fy2027_is_half_known():
    """Tester 2: H1 actuals so FY27E is half known."""
    out = run(real("deck"), model(DECK, years(2), ytd=True))
    assert (out["base_fy"], out["current"]["quarters"]) == (2026, 2)
    first = out["cases"]["base"]["years"][0]
    assert first["actual"]["quarters"] == 2 and first["remainder"]["quarters"] == 2
    # H2 FY2026 (the base year less its H1) is what the remainder grows from
    assert out["current"]["remainder_prior"]["revenue"] == pytest.approx(
        5471.5e6 - (964.5e6 + 1431.2e6), rel=2e-3)
    # growth and margin from the TTM to FY2027 Q2; FCF margin H2 FY2026's: working capital is
    # seasonal (H1 -1.9%, H2 35.7%, the TTM 18.9%)
    d = out["cases"]["base"]["defaults"][0]
    assert (d["growth"], d["margin"], d["fcfMargin"]) == (6.3, 23.6, 35.7)
    assert round(out["run_rate"]["fcf_margin"] * 100, 1) == 18.9


def test_nvidia_remainder_defaults_to_the_trailing_twelve_months():
    """Owner, 2026-11-09: H2 FY2027's default margin was 75.0% (H2 FY2026's 64.2% moved by this
    year's +10.8 points), above any quarter NVIDIA reported. It now starts from the TTM to FY2027
    Q2 like any first year: +83.4% growth, a 65.2% operating margin."""
    out = run(real("nvda"), model(NVDA, years(3), ytd=True))
    cur, rr = out["current"], out["run_rate"]
    assert (out["base_fy"], cur["fy"], cur["quarters"], rr["period"]) == (
        2026, 2027, 2, "FY2027 Q2")
    d = out["cases"]["base"]["defaults"]
    assert (d[0]["growth"], d[0]["margin"]) == (83.4, 65.2)
    assert (d[0]["growth"], d[0]["margin"]) == (round(rr["growth"] * 100, 1),
                                                round(rr["margin"] * 100, 1))
    # not the old rule's numbers: the year to date's growth, last H2's margin + this year's change
    old_margin = (cur["remainder_prior"]["margin"] + cur["actual"]["margin"]
                  - cur["prior"]["margin"]) * 100
    assert round(old_margin, 1) == 75.0 and d[0]["margin"] < old_margin - 9
    assert round(cur["actual"]["growth"] * 100, 1) == 95.8 != d[0]["growth"]
    # the FCF margin is still H2 FY2026's; years 2 and on fade from the same TTM as before, to
    # the 5-year median margin since Morgan 16 (54.1%, not FY2023's write-down year's mean, 46.0%)
    assert d[0]["fcfMargin"] == round(cur["remainder_prior"]["fcf_margin"] * 100, 1) == 45.6
    assert [(y["growth"], y["margin"]) for y in d[1:]] == [(44.2, 59.7), (24.6, 56.9)]
    # Reset's year 1, projected: H1 as reported plus H2 at the TTM's rates; the full year's margin
    # lies between the two halves'
    y = run(real("nvda"), model(NVDA, d, ytd=True))["cases"]["base"]["years"][0]
    assert y["remainder"]["margin"] == pytest.approx(0.652)
    assert y["remainder"]["revenue"] == pytest.approx(cur["remainder_prior"]["revenue"] * 1.834)
    assert 0.652 < y["margin"] < cur["actual"]["margin"]
    assert out["cases"]["base"]["user_set"] == [True, True, True]
    assert run(real("nvda"), model(NVDA, d, ytd=True))["cases"]["base"]["user_set"] == [
        False, False, False]


def test_a_model_saved_before_f2_keeps_year_one_a_full_year_guess():
    state = {"companyId": VRT, "historyYears": 8, "assumptions": [
        {"growth": 40, "margin": 21, "note": "AI capex"}, {"growth": 20, "margin": 21}]}
    saved = validate_state("model", state)
    assert saved == state  # validates and saves unchanged
    out = run(real("vrt"), saved)
    assert out["ytd"] is False
    y = out["cases"]["base"]["years"][0]
    assert "actual" not in y and y["growth"] == 0.4 and y["margin"] == 0.21
    assert y["revenue"] == pytest.approx(out["history"][-1]["revenue"] * 1.4)
    assert [c["growth"] for c in out["cases"]["base"]["check"]["years"]] == [40, 20]
    # what it would be built from actuals without changing the year
    implied = out["cases"]["base"]["implied_remainder"]
    rebuilt = run(real("vrt"), model(VRT, [implied, {"growth": 20, "margin": 21}], ytd=True))
    # each case has its own (bear and bull previewed here)
    assert out["cases"]["bear"]["implied_remainder"]["growth"] < implied["growth"]
    assert "implied_remainder" not in rebuilt["cases"]["base"]
    assert rebuilt["cases"]["base"]["years"][0]["revenue"] == pytest.approx(y["revenue"])
    assert rebuilt["cases"]["base"]["years"][0]["margin"] == pytest.approx(0.21)


def test_no_quarter_reported_yet_makes_the_remainder_the_whole_year():
    inputs = real("vrt")
    inputs["quarters"] = [r for r in inputs["quarters"] if r["fiscal_year"] != 2026]
    out = run(inputs, model(VRT, years(1, 30, 20), ytd=True))
    assert out["current"]["quarters"] == 0
    y = out["cases"]["base"]["years"][0]
    assert "actual" not in y and y["growth"] == 0.3
    # and the defaults are B7's: the run rate
    assert out["cases"]["base"]["defaults"][0]["growth"] == round(
        out["run_rate"]["growth"] * 100, 1)


# --- the lines below operating income ----------------------------------------------------------

def test_eps_shares_and_fcf_follow_the_rates():
    out = run(real("deck"), model(DECK, [
        {"growth": 5, "margin": 22, "nonOperating": -1, "taxRate": 23, "shareChange": -8,
         "fcfMargin": 20}, {"growth": 5, "margin": 22}]))
    base = out["history"][-1]
    y1, y2 = out["cases"]["base"]["years"]
    assert y1["defaulted"] == [] and y2["defaulted"] == ["nonOperating", "taxRate",
                                                         "shareChange", "fcfMargin"]
    rev = base["revenue"] * 1.05
    pretax = rev * 0.22 + rev * 0.01
    assert y1["net_income"] == pytest.approx(pretax * 0.77)
    assert y1["shares"] == pytest.approx(base["shares"] * 0.92)
    assert y1["eps"] == pytest.approx(y1["net_income"] / y1["shares"])
    assert y1["fcf"] == pytest.approx(rev * 0.2)
    # tester 2: EPS growth with an 8% share reduction
    assert y1["eps_growth"] == pytest.approx(y1["eps_adjusted"] / base["eps_adjusted"] - 1)
    # the defaults come from history: Deckers' diluted shares fell 8.4% in a year
    d = out["cases"]["base"]["defaults"][1]
    assert d["shareChange"] == -8.4 and out["run_rate"]["share_source"] == "FY2027 Q2"
    assert out["run_rate"]["tax_source"] == "ttm" and 20 < d["taxRate"] < 25
    assert y2["inputs"]["shareChange"] == -8.4


def test_history_has_eps_shares_and_fcf_from_the_10k_and_ttm():
    out = run(real("deck"), model(DECK, years(1)))
    fy26 = out["history"][-1]
    assert (fy26["fy"], fy26["eps"], fy26["shares"]) == (2026, 7.02, 145805000.0)
    assert fy26["fcf"] == pytest.approx(fy26["operating_cash_flow"] - fy26["capex"], rel=1e-6)
    assert fy26["share_change"] == pytest.approx(145.805 / 152.67 - 1, rel=1e-3)
    ttm = out["ttm"]
    assert ttm["period"] == "FY2027 Q2" and ttm["eps"] is None and ttm["shares"] == 135.3e6


# --- one-off adjustments --------------------------------------------------------------------

REFUND = {"name": "IEEPA refunds", "fy": 2027, "quarter": 2, "kind": "one_off", "amount": 46e6}


def test_a_one_off_is_excluded_from_margins_and_growth():
    """Tester 2: strip $46M of refunds from margin."""
    plain = run(real("deck"), model(DECK, years(2), ytd=True))
    out = run(real("deck"), model(DECK, years(2), ytd=True, adjustments=[REFUND]))
    a, p = out["current"]["actual"], plain["current"]["actual"]
    assert a["operating_income"] == p["operating_income"]  # reported stays reported
    assert a["operating_income_adjusted"] == pytest.approx(p["operating_income"] - 46e6)
    assert a["margin"] < p["margin"]
    # the remainder's default margin is the clean TTM's (the refund's Q2 is inside it)
    assert out["cases"]["base"]["defaults"][0]["margin"] < \
        plain["cases"]["base"]["defaults"][0]["margin"]
    y = out["cases"]["base"]["years"][0]
    assert y["one_offs"] == 46e6
    assert y["operating_income"] == pytest.approx(y["operating_income_adjusted"] + 46e6)
    assert y["net_income_adjusted"] == pytest.approx(
        y["net_income"] - 46e6 * (1 - y["tax_rate"]))
    # the TTM run rate excludes it too (FY2027 Q2 is inside the TTM to FY2027 Q2)
    assert out["run_rate"]["margin"] < plain["run_rate"]["margin"]


def test_a_one_off_in_a_history_year_shows_reported_and_adjusted():
    out = run(real("vrt"), model(VRT, years(1), adjustments=[
        {"name": "Litigation", "fy": 2024, "quarter": 3, "kind": "one_off", "amount": -100e6}]))
    fy24 = next(y for y in out["history"] if y["fy"] == 2024)
    assert fy24["operating_income_adjusted"] == pytest.approx(fy24["operating_income"] + 100e6)
    assert fy24["margin"] > fy24["margin_reported"]
    assert fy24["eps_adjusted"] > fy24["eps"]


def test_organic_growth_excludes_the_users_acquired_revenue():
    acquired = {"name": "UIG", "fy": 2026, "quarter": 4, "kind": "acquired", "amount": 500e6}
    out = run(real("vrt"), model(VRT, years(2, 30, 22), ytd=True, adjustments=[acquired]))
    y = out["cases"]["base"]["years"][0]
    base = out["history"][-1]["revenue"]
    assert y["organic_growth"] == pytest.approx((y["revenue"] - 500e6) / base - 1)
    assert out["cases"]["base"]["years"][1]["organic_growth"] == pytest.approx(0.3)
    # without such entries there's no organic line
    assert run(real("vrt"), model(VRT, years(1)))["history"][-1]["organic_growth"] is None


# --- valuation --------------------------------------------------------------------------------

def test_a_typed_price_gives_multiples_for_the_ttm_and_each_case_only():
    """morgan4 finding 5: today's $185 over FY2021's EPS read "P/E FY2021 560.6x". A past year
    gets no multiple; the current twelve months (here the TTM column to FY2026 Q3) and the
    forecasts do."""
    out = run(real("vrt"), model(VRT, years(2), ytd=True,
                                 price={"value": 150, "date": "2026-11-06"},
                                 scenarios={"bear": years(2, 0, 15), "bull": years(2, 40, 25)}))
    v = out["valuation"]
    shares = 385027873
    assert v["market_cap"] == pytest.approx(150 * shares)
    assert v["net_debt"]["value"] == -783200000 and v["net_debt"]["period_end"] == "2026-09-30"
    assert v["enterprise_value"] == pytest.approx(150 * shares - 783.2e6)
    assert out["ttm"] is not None and v["history"] == []
    # Riley 11: one P/E method in every column, price over diluted EPS; the TTM column's EPS is its
    # net income over the latest quarter's diluted shares (it read market cap / net income)
    assert v["ttm"]["pe"] == pytest.approx(
        150 / (out["ttm"]["net_income_adjusted"] / out["ttm"]["shares"]))
    assert v["ttm"]["ev_ebit"] == pytest.approx(
        v["enterprise_value"] / out["ttm"]["operating_income_adjusted"])
    assert set(v["cases"]) == {"bear", "base", "bull"}
    bull = out["cases"]["bull"]["years"][1]
    assert v["cases"]["bull"][1]["fcf_yield"] == pytest.approx(bull["fcf"] / v["market_cap"])


def through_q4(inputs: dict) -> dict:
    """Vertiv's inputs as they read before its FY2026 Q1: the newest TTM ends at FY2025 Q4, so
    there is no TTM column and the base year is the current twelve months."""
    out = json.loads(json.dumps(inputs))
    for key in ("ttm", "quarters"):
        out[key] = [r for r in out[key] if r["fiscal_year"] <= 2025]
    return out


def test_with_no_ttm_column_the_base_year_is_the_current_twelve_months():
    out = run(through_q4(real("vrt")), model(VRT, years(2),
                                             price={"value": 150, "date": "2026-11-06"}))
    v = out["valuation"]
    fy25 = out["history"][-1]
    assert out["ttm"] is None and v["ttm"] is None and fy25["fy"] == 2025
    assert [m["fy"] for m in v["history"]] == [2025]
    assert v["history"][0]["pe"] == pytest.approx(150 / fy25["eps_adjusted"])
    assert v["history"][0]["ev_ebit"] == pytest.approx(
        v["enterprise_value"] / fy25["operating_income_adjusted"])
    assert len(v["cases"]["base"]) == 2


def as_euros(inputs: dict, debt_age: str | None = None) -> dict:
    """Vertiv's inputs as a euro filer's would read after 056: every amount row in EUR (the
    fixture's rows were captured without their unit, which is USD), and net debt's latest
    reading as gold.latest_reading gives a euro amount (other_currency), with Store.model_inputs'
    unit and period status (`debt_age`)."""
    out = json.loads(json.dumps(inputs))
    for key in ("ttm", "quarters", "annual"):
        for r in out[key]:
            r["unit"] = ("shares" if "shares" in r["metric"] else
                         "EUR/shares" if r["metric"].startswith("eps") else "EUR")
    out["latest"]["net_debt"].update({
        "status": "other_currency", "unit": "EUR", "period_status": debt_age,
        "reason": "reported in EUR, so it isn't sorted, filtered or averaged with US-dollar "
                  "figures"})
    out["latest"]["shares_outstanding"].update({"unit": "shares", "period_status": None})
    return out


def test_a_price_in_another_currency_than_the_figures_gives_no_multiple():
    """057: a dollar price beside euro EPS divides nothing; the run says both currencies. A
    price saved before (no currency) is a dollar one. Typed in euros, the multiples are there."""
    euros = as_euros(real("vrt"))
    assert run(real("vrt"), model(VRT, years(1)))["currency"] == "USD"
    for price in ({"value": 150, "date": "2026-11-06"},
                  {"value": 150, "date": "2026-11-06", "currency": "USD"}):
        out = run(euros, model(VRT, years(2), price=price))
        v = out["valuation"]
        assert out["currency"] == "EUR"
        assert v["currency_mismatch"] == {"price": "USD", "figures": "EUR"}
        assert v["market_cap"] == pytest.approx(150 * 385027873)
        assert v["market_cap_currency"] == "USD"
        assert v["enterprise_value"] is None and v["ev_note"] == "currency"
        assert v["history"] == [] and v["ttm"] is None and v["cases"] == {}
    v = run(euros, model(VRT, years(2), price={"value": 150, "date": "2026-11-06",
                                               "currency": "EUR"}))["valuation"]
    assert "currency_mismatch" not in v and v["market_cap_currency"] == "EUR"
    assert v["ttm"]["pe"] is not None and v["enterprise_value"] is not None
    # and a dollar company's run is as before, with the same multiples
    usd = run(real("vrt"), model(VRT, years(2), price={"value": 150, "date": "2026-11-06"}))
    assert "currency_mismatch" not in usd["valuation"]
    assert usd["valuation"]["ttm"] == v["ttm"] and usd["valuation"]["cases"] == v["cases"]


def test_a_euro_price_gives_a_euro_companys_ev_and_multiples():
    """Pre-ship review: net debt in euros reads `other_currency` (056: not a dollar figure), which
    the valuation took for unusable, so EV was blank for every non-USD company even at a price in
    its own currency. With the price, the figures and net debt in one currency, net debt is read
    by its age alone, as a dollar company's is, and EV and the EV multiples are in that currency."""
    price = {"value": 150, "date": "2026-11-06", "currency": "EUR"}
    out = run(as_euros(real("vrt")), model(VRT, years(2), price=price))
    v = out["valuation"]
    cap = 150 * 385027873
    assert v["market_cap"] == pytest.approx(cap) and v["market_cap_currency"] == "EUR"
    assert v["enterprise_value"] == pytest.approx(cap - 783200000) and v["ev_note"] is None
    assert v["net_debt"]["value"] == -783200000 and v["net_debt"]["status"] is None
    assert v["net_debt"]["reason"] is None
    assert v["ttm"]["ev_ebit"] == pytest.approx(
        v["enterprise_value"] / out["ttm"]["operating_income_adjusted"])
    assert v["cases"]["base"][0]["ev_revenue"] is not None
    # read by its age, as a dollar company's: an earlier quarter still gives EV, an old one not
    behind = run(as_euros(real("vrt"), "behind"), model(VRT, years(2), price=price))["valuation"]
    assert behind["enterprise_value"] == pytest.approx(cap - 783200000)
    assert behind["net_debt"]["status"] == "behind"
    old = run(as_euros(real("vrt"), "old"), model(VRT, years(2), price=price))["valuation"]
    assert old["enterprise_value"] is None and old["ev_note"] == "old"
    # a dollar price beside euro figures still divides nothing
    usd = run(as_euros(real("vrt")), model(VRT, years(2), price={**price, "currency": "USD"}))
    assert usd["valuation"]["enterprise_value"] is None
    assert usd["valuation"]["ev_note"] == "currency"
    # net debt in a third currency (never the case since 056's one currency per company) or from
    # an older Store without its age is left out, as before
    odd = as_euros(real("vrt"))
    odd["latest"]["net_debt"]["unit"] = "GBP"
    assert run(odd, model(VRT, years(2), price=price))["valuation"]["ev_note"] == "other_currency"
    del odd["latest"]["net_debt"]["period_status"]
    odd["latest"]["net_debt"]["unit"] = "EUR"
    assert run(odd, model(VRT, years(2), price=price))["valuation"]["ev_note"] == "other_currency"


def test_a_price_currency_is_three_capitals():
    for bad in ("usd", "EURO", "€"):
        with pytest.raises(Exception, match="currency"):
            model(VRT, years(1), price={"value": 1.0, "date": "2026-11-06", "currency": bad})


def test_ev_needs_a_current_net_debt():
    """Deckers' latest net debt is from 2014 (old, 035): no EV, and why."""
    v = run(real("deck"), model(DECK, years(1), price={"value": 100, "date": "2026-11-06"}))[
        "valuation"]
    assert v["net_debt"]["status"] == "old" and v["net_debt"]["period_end"] == "2014-09-30"
    assert v["enterprise_value"] is None and v["ev_note"] == "old"
    assert v["ttm"]["ev_ebit"] is None and v["ttm"]["pe"] is not None
    assert v["cases"]["base"][0]["ev_revenue"] is None
    inputs = real("deck")
    inputs["latest"].pop("net_debt")
    assert run(inputs, model(DECK, years(1), price={"value": 100, "date": "2026-11-06"}))[
        "valuation"]["ev_note"] == "absent"


def test_multiples_over_losses_are_not_meaningful():
    out = run(real("vrt"), model(VRT, years(1, 10, -5), price={"value": 10,
                                                               "date": "2026-11-06"}))
    m = out["valuation"]["cases"]["base"][0]
    assert m["pe"] == "n/m" and m["ev_ebit"] == "n/m"
    assert run(real("vrt"), model(VRT, years(1)))["valuation"] is None  # no price, no valuation


# --- shapes ------------------------------------------------------------------------------------

def test_bear_and_bull_are_previewed_until_the_user_adds_them():
    out = run(real("nvda"), model(NVDA, years(2, 30, 60)))
    assert out["cases"]["bear"]["preview"] and out["cases"]["bull"]["preview"]
    assert out["cases"]["bear"]["years"][0]["inputs"]["growth"] == 15.0
    added = run(real("nvda"), model(NVDA, years(2, 30, 60), scenarios={"bear": years(2, 1, 1)}))
    assert not added["cases"]["bear"]["preview"] and added["cases"]["bull"]["preview"]


def test_no_history_projects_nothing():
    out = run({"ttm": [], "quarters": [], "annual": [], "latest": {}}, model(NVDA, years(2)))
    assert out["history"] == [] and out["base_fy"] is None and out["current"] is None
    assert out["cases"]["base"]["years"] == []
    assert [d["growth"] for d in out["cases"]["base"]["defaults"]] == [0.0, 0.0]


def test_a_split_or_merger_breaks_the_share_history_rather_than_reading_as_buybacks():
    """Deckers' 6-for-1 (2024): its FY2023 10-K count is restated, FY2021-22 aren't. NVIDIA's
    4- and 10-for-1 the same. No +476% share change, no -80% EPS growth, no pre-split P/E."""
    out = run(real("deck"), model(DECK, years(1), price={"value": 100, "date": "2026-11-06"}))
    by = {y["fy"]: y for y in out["history"]}
    assert by[2023]["share_break"] and by[2023]["share_change"] is None
    assert by[2023]["eps_growth"] is None
    assert by[2022]["earlier_share_basis"] == 2023 and by[2024]["earlier_share_basis"] is None
    assert by[2024]["share_change"] == pytest.approx(156.285 / 160.111 - 1, rel=1e-4)
    # no past year's P/E at all now (finding 5), so none on the pre-split basis either
    assert out["valuation"]["history"] == [] and out["valuation"]["ttm"]["pe"] is not None
    nvda = {y["fy"]: y for y in run(real("nvda"), model(NVDA, years(1)))["history"]}
    assert [fy for fy, y in nvda.items() if y["share_break"]] == [2020, 2023]
    # the run rate's share change was never fooled: the latest quarter against a year earlier
    assert out["run_rate"]["share_change"] == pytest.approx(135.3 / 147.709 - 1, rel=1e-2)


def test_the_year_to_dates_adjusted_net_income_takes_the_one_off_off_after_tax():
    a = run(real("deck"), model(DECK, years(1), ytd=True, adjustments=[REFUND]))["current"][
        "actual"]
    rate = a["income_tax"] / a["pretax_income"]
    assert a["net_income_adjusted"] == pytest.approx(a["net_income"] - 46e6 * (1 - rate))


def test_the_long_run_is_robust_to_a_pandemic_year():
    """Morgan 16: Booking's base case faded its margin toward a 5-year mean with FY2021 in it, and
    the check compared growth with a 31.7% "5-year CAGR" measured from the FY2020 trough. The long
    run is the median margin, and a CAGR never starts in a trough (revenue more than TROUGH_DROP
    below the best of the two years before): it starts at that year instead."""
    from query_api.model_maths import TROUGH_DROP, stats

    def year(fy, revenue, margin):
        return {"fy": fy, "revenue": revenue, "margin": margin, "fcf_margin": margin}

    bkng = [year(2018, 14.5, 0.32), year(2019, 15.07, 0.35), year(2020, 6.80, 0.01),
            year(2021, 10.96, 0.228), year(2022, 17.09, 0.299), year(2023, 21.37, 0.273),
            year(2024, 23.74, 0.318), year(2025, 26.04, 0.328)]
    st = stats(bkng)
    assert st["cagr_years"] == 6 and st["cagr"] == pytest.approx((26.04 / 15.07) ** (1 / 6) - 1)
    assert st["avg_margin"] == pytest.approx(0.299)          # the median of FY2021-FY2025
    assert st["_flat"]["cagr"] == pytest.approx((26.04 / 6.80) ** (1 / 5) - 1)   # 30.8%
    assert st["_flat"]["margin"] == pytest.approx((0.228 + 0.299 + 0.273 + 0.318 + 0.328) / 5)
    # a dip under TROUGH_DROP is growth's own history
    dip = [year(2019, 10.0, 0.2), year(2020, 10.0 * (1 - TROUGH_DROP) + 0.01, 0.2),
           year(2025, 12.0, 0.2)]
    assert stats(dip)["cagr_years"] == 5
    # a cruise line's -371% year doesn't drag the long run below zero
    ccl = [year(2021, 1.9, -3.714), year(2022, 12.2, -0.36), year(2023, 21.6, 0.091),
           year(2024, 25.0, 0.143), year(2025, 26.6, 0.168)]
    assert stats(ccl)["avg_margin"] == pytest.approx(0.091)
