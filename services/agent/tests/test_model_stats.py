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
    history_stats,
    scenario_assumptions,
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
