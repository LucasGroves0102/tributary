"""Per-share figures on one share basis (query_api.per_share; Riley, test 6; owner decisions 1 and
2): split-adjusted history, derived fiscal Q4 and TTM, as filed one parameter away. Fixtures are
production's Store.per_share_inputs answers (hermes, 2026-11-15) for NVIDIA (4-for-1 in 2021,
10-for-1 in 2024), JPMorgan (preferred dividends) and Deckers (6-for-1 in 2024)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.per_share import compute, splits

from .conftest import SECRET
from .test_api import NVDA, FakeAuthorizer

FIXTURES = Path(__file__).parent / "fixtures" / "per_share"


def inputs(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def at(rows: list[dict], metric: str, end: str) -> dict:
    return next(r for r in rows if r["metric"] == metric and r["period_end"] == end)


def test_splits_come_from_the_split_notes_with_their_first_restating_filing():
    got = splits(inputs("nvda")["findings"])
    assert [(s["label"], s["first_filed"].isoformat()) for s in got] == [
        ("4-for-1", "2021-08-20"), ("10-for-1", "2024-08-28")]
    assert [s["label"] for s in splits(inputs("deck")["findings"])] == ["6-for-1"]
    assert splits(inputs("jpm")["findings"]) == []


def test_every_year_is_on_todays_share_basis_with_what_was_filed_kept():
    """Riley 1: FY2019 $6.63 on 625M shares, FY2020 $1.13 on 2.47B, FY2023 $0.17 on 25.07B."""
    rows = compute(inputs("nvda"), "annual")["rows"]
    fy19 = at(rows, "eps_diluted", "2019-01-27")
    assert (fy19["value"], fy19["as_filed"]) == ("0.1658", "6.63")
    assert [s["label"] for s in fy19["splits"]] == ["4-for-1", "10-for-1"]
    fy20 = at(rows, "eps_diluted", "2020-01-26")  # restated by NVIDIA itself after the 4-for-1
    assert (fy20["value"], fy20["as_filed"]) == ("0.113", "1.13")
    assert [s["label"] for s in fy20["splits"]] == ["10-for-1"]
    fy23 = at(rows, "eps_diluted", "2023-01-29")  # restated after both: as filed
    assert (fy23["value"], fy23["splits"]) == (fy23["as_filed"], [])
    shares19 = at(rows, "weighted_shares_diluted", "2019-01-27")
    assert Decimal(shares19["value"]) == Decimal(shares19["as_filed"]) * 40
    # one basis: every year's diluted shares within 10% of the next year's
    shares = sorted((r["period_end"], Decimal(r["value"])) for r in rows
                    if r["metric"] == "weighted_shares_diluted" and r["period_end"] >= "2018")
    assert all(abs(b / a - 1) < Decimal("0.1")
               for (_, a), (_, b) in zip(shares, shares[1:], strict=False))


def test_as_filed_is_each_filings_own_figure():
    rows = compute(inputs("nvda"), "annual", "as_filed")["rows"]
    assert at(rows, "eps_diluted", "2019-01-27")["value"] == "6.63"
    assert all(r["splits"] == [] for r in rows)


def test_a_quarter_the_company_never_restated_takes_the_split_after_its_filing():
    """NVIDIA's Apr 2023 quarter is only ever filed before the 10-for-1 (gold: 0.82 on 2.49B)."""
    rows = compute(inputs("nvda"), "quarter")["rows"]
    q = at(rows, "eps_diluted", "2023-04-30")
    assert (q["value"], q["as_filed"], [s["label"] for s in q["splits"]]) == ("0.082", "0.82",
                                                                               ["10-for-1"])
    assert at(rows, "weighted_shares_diluted", "2023-04-30")["value"] == "24900000000"


def test_fiscal_q4_is_derived_from_its_net_income_and_derived_shares():
    """Riley 7: Q4 EPS read "—". Q4 = net income / Q4 diluted shares (owner decision 2)."""
    rows = compute(inputs("nvda"), "quarter")["rows"]
    q4 = at(rows, "eps_diluted", "2026-01-25")
    assert q4["value_source"] == "derived" and q4["fiscal_period"] == "FY2026 Q4"
    assert q4["derivation"]["method"] == "q4_eps"
    roles = [i["role"] for i in q4["derivation"]["inputs"]]
    assert roles == ["Q4 net income", "Q4 shares", "year"]
    # the year less nine months, to the cent: $4.90 - ($0.76 + $1.08 + $1.30) = $1.76
    assert abs(Decimal(q4["value"]) - Decimal("1.76")) < Decimal("0.005")
    shares = at(rows, "weighted_shares_diluted", "2026-01-25")
    assert shares["derivation"]["method"] == "q4_shares"
    assert 24_400_000_000 < int(shares["value"]) < 24_483_000_000  # below Q3's: buybacks
    assert "note" not in q4["derivation"]  # $0.17 rounding isn't a preferred dividend


def test_preferred_dividends_come_off_a_derived_q4():
    """JPMorgan's Q4 2024 diluted EPS was $4.81; net income / shares alone is $4.93."""
    rows = compute(inputs("jpm"), "quarter")["rows"]
    q4 = at(rows, "eps_diluted", "2024-12-31")
    assert abs(Decimal(q4["value"]) - Decimal("4.81")) < Decimal("0.05")
    assert "available to common" in q4["derivation"]["note"]


def test_ttm_eps_is_the_four_quarters_added():
    """Riley 6: TTM EPS read "n/a". The market convention: the four quarters' EPS added."""
    out = compute(inputs("nvda"), "ttm")
    t = at(out["rows"], "eps_diluted", "2026-01-25")
    parts = [Decimal(i["value"]) for i in t["derivation"]["inputs"]]
    assert len(parts) == 4 and Decimal(t["value"]) == sum(parts)
    assert t["derivation"]["inputs"][-1].get("derived") is True  # its Q4
    assert abs(Decimal(t["value"]) - Decimal("4.90")) < Decimal("0.01")  # the 10-K's year
    shares = at(out["rows"], "weighted_shares_diluted", "2026-01-25")
    assert shares["derivation"]["method"] == "ttm_shares"


def test_as_filed_never_adds_quarters_across_a_split():
    adjusted = {r["period_end"] for r in compute(inputs("deck"), "ttm")["rows"]
                if r["metric"] == "eps_diluted"}
    filed = {r["period_end"] for r in compute(inputs("deck"), "ttm", "as_filed")["rows"]
             if r["metric"] == "eps_diluted"}
    assert filed < adjusted  # the windows across the 6-for-1 drop out as filed
    deck = compute(inputs("deck"), "annual")["rows"]
    fy22 = at(deck, "eps_diluted", "2022-03-31")
    assert (fy22["value"], fy22["as_filed"]) == ("2.71", "16.26")


def test_a_q4_whose_year_and_quarters_disagree_is_left_out():
    data = inputs("nvda")
    for r in data["annual"]:  # the year's EPS off by a dollar: no one share count fits
        if r["metric"] == "eps_diluted" and r["period_end"] == "2026-01-25":
            r["value"] = str(Decimal(r["value"]) + 1)
    rows = compute(data, "quarter")["rows"]
    assert not [r for r in rows if r["metric"] == "eps_diluted" and r["period_end"] == "2026-01-25"]


class Store:
    def __init__(self):
        self.calls = []

    def per_share_inputs(self, user_id, company_id):
        self.calls.append(company_id)
        return inputs("nvda")


@pytest.fixture
def client():
    store = Store()
    auth = FakeAuthorizer({"alice": {NVDA: {"financials"}}})
    return TestClient(create_app(auth, store, SECRET)), store


def test_the_route_checks_the_tier_first_and_cites_each_figure(client, token):
    c, store = client
    r = c.get(f"/v1/companies/{NVDA}/per-share", headers=token("bob"))
    assert r.status_code == 403 and store.calls == []
    r = c.get(f"/v1/companies/{NVDA}/per-share", params={"view": "ttm", "quarters": 4},
              headers=token("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["view"], body["shares"]) == ("ttm", "adjusted")
    assert [s["label"] for s in body["splits"]] == ["4-for-1", "10-for-1"]
    by = {}
    for row in body["rows"]:
        by.setdefault(row["metric"], []).append(row)
        assert row["citation"]["filing_url"].startswith("https://www.sec.gov/Archives/")
        assert "filer_cik" not in row["citation"] and "calendar_primary" not in row
    assert all(len(v) <= 4 for v in by.values())
    filed = c.get(f"/v1/companies/{NVDA}/per-share",
                  params={"view": "annual", "shares": "as_filed"}, headers=token("alice")).json()
    assert at(filed["rows"], "eps_diluted", "2019-01-27")["value"] == "6.63"
