"""model_stats.py is a port of the web app's models/model.ts: the same fixture, the same
numbers (services/web/src/models/stats-fixture.json, also checked by model.test.ts)."""

import json
from pathlib import Path

import pytest

from tributary_agent.model_stats import (
    GROWTH_FLAG_POINTS,
    SCENARIOS,
    HistoryYear,
    annual_history,
    check_assumptions,
    default_assumptions,
    history_stats,
    old_default,
    run_rate,
    scenario_assumptions,
    user_set,
)

FIXTURE = Path(__file__).parents[2] / "web" / "src" / "models" / "stats-fixture.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


def close(a, b):
    assert (a is None and b is None) or a == pytest.approx(b, rel=1e-12, abs=1e-15)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_same_numbers_as_the_web_app(case):
    history = annual_history(case["rows"])
    assert [{"fy": y.fy, "periodEnd": y.period_end, "revenue": y.revenue,
             "operatingIncome": y.operating_income} for y in history] == \
        case["expected"]["history"]
    got, want = check_assumptions(history, case["assumptions"]), case["expected"]
    assert got["base_fy"] == want["base_fy"]
    for key in ("cagr", "avg_margin", "last_growth", "last_margin"):
        close(got["stats"][key], want["stats"][key])
    assert got["stats"]["cagr_years"] == want["stats"]["cagr_years"]
    assert got["stats"]["margin_years"] == want["stats"]["margin_years"]
    close(got["margin_min"], want["margin_min"])
    close(got["margin_max"], want["margin_max"])
    assert len(got["years"]) == len(want["years"])
    for g, w in zip(got["years"], want["years"], strict=True):
        assert (g["fy"], g["growth"], g["margin"], g["growth_flag"], g["margin_flag"]) == \
            (w["fy"], w["growth"], w["margin"], w["growth_flag"], w["margin_flag"])
        close(g["growth_vs_cagr"], w["growth_vs_cagr"])


def test_hand_computed_case():
    """100 -> 161.051 over 5 years is 10% a year; margins 20, 20, 10, 20, 10 average 16%."""
    case = CASES[0]
    s = history_stats(annual_history(case["rows"]))
    assert s.cagr == pytest.approx(0.10) and s.cagr_years == 5
    assert s.avg_margin == pytest.approx(0.16) and s.margin_years == 5
    r = check_assumptions(annual_history(case["rows"]), case["assumptions"])
    assert (r["margin_min"], r["margin_max"]) == (pytest.approx(0.1), pytest.approx(0.2))
    assert [(y["fy"], y["growth_flag"], y["margin_flag"]) for y in r["years"]] == [
        (2026, "above", "above"), (2027, None, None), (2028, "below", "below"),
        (2029, None, "above")]


def test_flag_boundaries():
    h = [HistoryYear(2023, "2023-12-31", 100.0, 10.0), HistoryYear(2024, "2024-12-31", 110.0, 22.0)]
    r = check_assumptions(h, [{"growth": 10 + GROWTH_FLAG_POINTS + 0.01, "margin": 20},
                              {"growth": 10 + GROWTH_FLAG_POINTS - 0.01, "margin": 9.99}])
    assert [(y["growth_flag"], y["margin_flag"]) for y in r["years"]] == [
        ("above", None), (None, "below")]


SCENARIO_CASES = json.loads(FIXTURE.read_text())["scenario_cases"]


@pytest.mark.parametrize("case", SCENARIO_CASES, ids=[c["name"] for c in SCENARIO_CASES])
def test_scenarios_pick_the_same_assumptions_as_the_web_app(case):
    for scenario in SCENARIOS:
        assert scenario_assumptions(case["state"], scenario) == case["expected"][scenario]


def test_the_base_case_is_what_old_models_hold():
    old = {"companyId": "0001045810", "assumptions": [{"growth": 5, "margin": 10}]}
    assert scenario_assumptions(old) == [{"growth": 5.0, "margin": 10.0}]
    assert scenario_assumptions(old, "bear") is None and scenario_assumptions(old, "x") is None


DEFAULT_CASES = json.loads(FIXTURE.read_text())["default_cases"]


@pytest.mark.parametrize("case", DEFAULT_CASES, ids=[c["name"] for c in DEFAULT_CASES])
def test_defaults_match_the_web_app(case):
    s = history_stats(annual_history(case["rows"]))
    run = run_rate(case["rows"])
    want = case["expected"]
    assert run.period == want["run_rate"]["period"]
    close(run.growth, want["run_rate"]["growth"])
    close(run.margin, want["run_rate"]["margin"])
    assert default_assumptions(s, case["years"], run) == want["defaults"]
    assert old_default(s) == want["old_default"]
    assert user_set(s, run, want["edited"]) == want["user_set"]


def test_defaults_start_at_the_run_rate_and_fade_to_the_long_run():
    """H5, #72: NVIDIA starts at its TTM growth (83.4%) and margin (65.2%), not the flat 5-yr
    66.9% and 46.0%; each year closes half the gap to 5% growth (the CAGR, capped) and the
    5-yr average margin. Deckers starts at its 6.3%, not its 16.5% CAGR."""
    by = {c["name"].split(":")[0]: c for c in DEFAULT_CASES}
    nvda = by["hyper-growth"]["expected"]["defaults"]
    assert [a["growth"] for a in nvda[:3]] == [83.4, 44.2, 24.6]
    assert [a["margin"] for a in nvda[:3]] == [65.2, 55.6, 50.8]
    assert by["steady"]["expected"]["defaults"][0] == {"growth": 6.3, "margin": 23.6}
    # Intel's 5-yr CAGR is negative: the long run is held at 0%, never a permanent decline
    assert by["shrinking"]["expected"]["defaults"][-1]["growth"] == 0.8
    assert by["synthetic"]["expected"]["defaults"][0] == {"growth": -20.0, "margin": 8.0}


def test_a_model_still_at_its_defaults_isnt_the_users_view():
    """#74: years equal to either default (this rule, or the flat one older models start
    from) are user_set False; a changed year is True."""
    case = DEFAULT_CASES[0]
    s = history_stats(annual_history(case["rows"]))
    run = run_rate(case["rows"])
    flat = [old_default(s)] * 3
    assert user_set(s, run, flat) == [False, False, False]
    assert user_set(s, run, default_assumptions(s, 3, run)) == [False, False, False]
    assert user_set(s, run, [{"growth": 30, "margin": 50}]) == [True]
