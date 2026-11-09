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


def test_bear_and_bull_start_from_the_base_case_by_b7s_spread():
    base = [{"growth": 83, "margin": 60, "taxRate": 18}, {"growth": 2, "margin": 10}]
    assert shifted(base, "bear") == [{"growth": 41.5, "margin": 48.0, "taxRate": 18},
                                     {"growth": -3.0, "margin": 7.0}]
    assert shifted(base, "bull") == [{"growth": 124.5, "margin": 72.0, "taxRate": 18},
                                     {"growth": 7.0, "margin": 13.0}]


# --- current year from actuals ----------------------------------------------------------------

def test_vertiv_fy2026_is_nine_months_reported_plus_q4():
    """Tester 1: FY26 as nine months actual plus Q4. The remainder's defaults: Q4 FY2025's
    revenue grown at the year to date's growth, its margin moved by this year's change so far."""
    out = run(real("vrt"), model(VRT, [{"growth": 50, "margin": 25}], ytd=True))
    cur = out["current"]
    assert (out["base_fy"], cur["fy"], cur["quarters"], cur["through"]) == (
        2025, 2026, 3, "2026-09-30")
    assert cur["actual"]["revenue"] == pytest.approx(2650e6 + 3274e6 + 4313.2e6, rel=1e-3)
    # the same nine months a year earlier, and the quarter left
    assert cur["actual"]["growth"] == pytest.approx(0.393, abs=1e-3)
    assert cur["remainder_prior"]["revenue"] == pytest.approx(2880e6, rel=1e-3)
    d = out["cases"]["base"]["defaults"][0]
    assert d["growth"] == 39.3
    assert d["margin"] == pytest.approx(
        round((cur["remainder_prior"]["margin"] + cur["actual"]["margin"]
               - cur["prior"]["margin"]) * 100, 1))
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
    # the remainder's default margin moves with the clean year to date
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

def test_a_typed_price_gives_multiples_on_history_and_each_case():
    out = run(real("vrt"), model(VRT, years(2), ytd=True,
                                 price={"value": 150, "date": "2026-11-06"},
                                 scenarios={"bear": years(2, 0, 15), "bull": years(2, 40, 25)}))
    v = out["valuation"]
    shares = 385027873
    assert v["market_cap"] == pytest.approx(150 * shares)
    assert v["net_debt"]["value"] == -783200000 and v["net_debt"]["period_end"] == "2026-09-30"
    assert v["enterprise_value"] == pytest.approx(150 * shares - 783.2e6)
    fy25 = out["history"][-1]
    assert v["history"][-1]["pe"] == pytest.approx(150 / fy25["eps_adjusted"])
    assert v["history"][-1]["ev_ebit"] == pytest.approx(
        v["enterprise_value"] / fy25["operating_income_adjusted"])
    assert v["ttm"]["pe"] == pytest.approx(v["market_cap"] / out["ttm"]["net_income_adjusted"])
    assert set(v["cases"]) == {"bear", "base", "bull"}
    bull = out["cases"]["bull"]["years"][1]
    assert v["cases"]["bull"][1]["fcf_yield"] == pytest.approx(bull["fcf"] / v["market_cap"])


def test_ev_needs_a_current_net_debt():
    """Deckers' latest net debt is from 2014 (old, 035): no EV, and why."""
    v = run(real("deck"), model(DECK, years(1), price={"value": 100, "date": "2026-11-06"}))[
        "valuation"]
    assert v["net_debt"]["status"] == "old" and v["net_debt"]["period_end"] == "2014-09-30"
    assert v["enterprise_value"] is None and v["ev_note"] == "old"
    assert v["history"][-1]["ev_ebit"] is None and v["history"][-1]["pe"] is not None
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
    pe = {m["fy"]: m["pe"] for m in out["valuation"]["history"]}
    assert pe[2022] is None and pe[2024] == pytest.approx(100 / 4.86)
    nvda = {y["fy"]: y for y in run(real("nvda"), model(NVDA, years(1)))["history"]}
    assert [fy for fy, y in nvda.items() if y["share_break"]] == [2020, 2023]
    # the run rate's share change was never fooled: the latest quarter against a year earlier
    assert out["run_rate"]["share_change"] == pytest.approx(135.3 / 147.709 - 1, rel=1e-2)


def test_the_year_to_dates_adjusted_net_income_takes_the_one_off_off_after_tax():
    a = run(real("deck"), model(DECK, years(1), ytd=True, adjustments=[REFUND]))["current"][
        "actual"]
    rate = a["income_tax"] / a["pretax_income"]
    assert a["net_income_adjusted"] == pytest.approx(a["net_income"] - 46e6 * (1 - rate))
