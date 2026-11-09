"""Statement callouts (F5, query_api.notes.callouts): migration 041's outlier and sign_flip tests on
the statement's own quarters, the newest one against the two before it. Series are production's
(gold, 2026-11-09)."""

import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.notes import (
    NEIGHBOUR_SPREAD,
    OUTLIER_FACTOR,
    SIGN_METRICS,
    callouts,
    is_outlier,
    is_sign_change,
)

from .conftest import SECRET

MIGRATION_041 = Path(__file__).parents[3] / "db" / "migrations" / "041_quality_findings.sql"

# NVIDIA's dividends paid: $243M a quarter, then $6,047M (FY2027 Q2, 6,290 - 243)
NVDA_DIVIDENDS = [(date(2025, 4, 27), 244e6), (date(2025, 7, 27), 244e6),
                  (date(2025, 10, 26), 244e6), (date(2026, 1, 25), 242e6),
                  (date(2026, 4, 26), 243e6), (date(2026, 7, 26), 6047e6)]
# JPMorgan's operating cash flow: a bank's moves with trading assets and loans
JPM_OCF = [(date(2024, 12, 31), 147758e6), (date(2025, 3, 31), -251839e6),
           (date(2025, 6, 30), 29547e6), (date(2025, 9, 30), -45214e6),
           (date(2025, 12, 31), 119724e6), (date(2026, 3, 31), -211761e6),
           (date(2026, 6, 30), -25283e6), (date(2026, 9, 30), 33161e6)]


def test_thresholds_are_041s():
    """One rule: the callouts use ops.check_financials' outlier and sign_flip tests as written."""
    sql = MIGRATION_041.read_text()
    outlier = sql[sql.index("'outlier', metric"):]
    assert f"<= {NEIGHBOUR_SPREAD} * least(abs(prev_value), abs(next_value))" in outlier
    assert (f"abs(value) >= {OUTLIER_FACTOR} * greatest(abs(prev_value), abs(next_value))"
            in outlier)
    flip = sql[sql.index("'sign_flip', metric"):sql.index("'outlier', metric")]
    listed = set(re.findall(r"'([a-z_]+)'", flip[flip.index("metric IN"):flip.index("AND value")]))
    assert SIGN_METRICS == listed | {"operating_cash_flow"}


def test_nvidia_dividends_step_is_called_out_on_the_newest_quarter():
    out = callouts("dividends_paid", NVDA_DIVIDENDS)
    assert out == [{
        "metric": "dividends_paid", "period_end": date(2026, 7, 26), "kind": "outlier",
        "edge": True, "value": 6047e6, "factor": 24.9,
        "beside": [{"period_end": date(2026, 1, 25), "value": 242e6},
                   {"period_end": date(2026, 4, 26), "value": 243e6}],
        "seasonal": False}]


def test_a_spike_inside_the_series_against_both_neighbours():
    s = [(date(2025, 3, 31), 10.0), (date(2025, 6, 30), 150.0), (date(2025, 9, 30), 12.0),
         (date(2025, 12, 31), 11.0)]
    out = callouts("share_buybacks", s)
    assert [(c["period_end"], c["kind"], c["edge"], c["factor"]) for c in out] == [
        (date(2025, 6, 30), "outlier", False, 12.5)]


def test_neighbours_that_disagree_are_no_baseline():
    assert not is_outlier(500, 10, 40)  # 4x apart
    assert not is_outlier(500, 10, -10)  # other signs
    assert not is_outlier(500, 0, 10)
    assert is_outlier(-200, -10, -20)  # a loss far outside losses


def test_jpmorgan_operating_cash_flow_swings_are_sign_changes():
    out = callouts("operating_cash_flow", JPM_OCF)
    assert [(c["period_end"], c["kind"], c["edge"]) for c in out] == [
        (date(2025, 3, 31), "sign", False), (date(2025, 6, 30), "sign", False),
        (date(2025, 9, 30), "sign", False), (date(2025, 12, 31), "sign", False),
        (date(2026, 9, 30), "sign", True)]
    assert all(c["factor"] is None and not c["seasonal"] for c in out)


def test_subtotals_change_sign_without_a_callout():
    assert callouts("financing_cash_flow", JPM_OCF) == []
    assert callouts("change_in_cash", JPM_OCF) == []
    assert is_sign_change(-5, 3, 4)
    assert not is_sign_change(-5, 3, -4) and not is_sign_change(0, 3, 4)


def test_a_spike_every_year_in_the_same_quarter_is_seasonal():
    """Deckers-like: December's operating cash flow is the year's, every year."""
    s = []
    for y in (2022, 2023, 2024, 2025):
        s += [(date(y, 3, 31), 20.0), (date(y, 6, 30), 15.0), (date(y, 9, 30), 18.0),
              (date(y, 12, 31), 400.0)]
    s.append((date(2026, 3, 31), 19.0))
    out = callouts("operating_cash_flow", s)
    assert out and all(c["kind"] == "outlier" and c["seasonal"] for c in out)


def test_too_short_a_series_says_nothing():
    assert callouts("dividends_paid", NVDA_DIVIDENDS[-2:]) == []


class SeriesStore:
    def __init__(self):
        self.asked: list = []

    def metric_names(self):
        return {"metrics": ["dividends_paid", "operating_cash_flow"],
                "derived_metrics": ["free_cash_flow"]}

    def financials(self, *_):
        return []

    def cadence(self, *_):
        return "quarterly"

    def metric_series(self, user_id, company_ids, metrics):
        self.asked.append((company_ids, metrics))
        return {("0001045810", "dividends_paid"): NVDA_DIVIDENDS}


class Yes:
    def can(self, *_):
        return True


def test_financials_adds_callouts_only_when_asked(token):
    store = SeriesStore()
    c = TestClient(create_app(Yes(), store, SECRET))  # type: ignore[arg-type]
    url = "/v1/companies/0001045810/financials?metrics=dividends_paid&metrics=free_cash_flow"
    plain = c.get(url, headers=token("alice")).json()
    assert "callouts" not in plain and store.asked == []
    asked = c.get(url + "&callouts=true", headers=token("alice")).json()
    assert store.asked == [(["0001045810"], ["dividends_paid"])]  # base metrics only
    assert [(x["metric"], x["period_end"], x["kind"]) for x in asked["callouts"]] == [
        ("dividends_paid", "2026-07-26", "outlier")]
    ttm = c.get(url + "&callouts=true&view=ttm", headers=token("alice")).json()
    assert "callouts" not in ttm


def test_financials_callouts_need_the_tier(token):
    class No(Yes):
        def can(self, *_):
            return False

    store = SeriesStore()
    c = TestClient(create_app(No(), store, SECRET))  # type: ignore[arg-type]
    r = c.get("/v1/companies/0001045810/financials?callouts=true", headers=token("alice"))
    assert r.status_code == 403 and store.asked == []
