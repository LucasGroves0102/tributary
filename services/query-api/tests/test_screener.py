"""Screener: compiled SQL holds no user input, and values reach only callers who may read them."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.screener import (
    COLUMNS,
    Screen,
    ScreenError,
    band_filters,
    compile_screen,
    parse_filter,
)

from .conftest import SECRET
from .test_api import NVDA, TSLA, FakeAuthorizer

GRANTS = {"alice": {NVDA: {"financials"}}, "bob": {NVDA: {"financials", "filings"},
                                                   TSLA: {"financials", "filings"}}}


def meta(cid, name):
    return {"company_id": cid, "name": name, "tickers": ["X"], "exchanges": ["Nasdaq"],
            "sic": "3674", "industry": "Semiconductors", "fiscal_year_end": "0126",
            "filer_category": "large_accelerated", "filer_categories": ["Large accelerated filer"],
            "state_of_incorporation": "DE"}


class ScreenStore:
    """Returns values for every company, as if RLS were off: layer 1 must still hide them."""

    def __init__(self):
        self.compiled = []

    def screen(self, user_id, compiled):
        self.compiled.append(compiled)
        rows = []
        for cid, name in ((NVDA, "NVIDIA CORP"), (TSLA, "Tesla, Inc.")):
            row = {**meta(cid, name), "total": 2}
            for i in compiled.slots.values():
                row[f"v{i}"] = Decimal("130497000000")
                row[f"src{i}"] = {"period_end": "2026-07-26", "accession_no": "a", "form": "10-Q",
                                  "filed": "2026-08-26"}
            rows.append(row)
        return rows


@pytest.fixture
def client():
    store = ScreenStore()
    c = TestClient(create_app(FakeAuthorizer(GRANTS), store, SECRET))
    c.store = store
    return c


def test_columns_cover_every_metric_and_view():
    assert COLUMNS["revenue_ttm"] == ("revenue", "ttm", "USD")
    assert COLUMNS["eps_diluted_q"] == ("eps_diluted", "quarter", "USD/shares")
    assert "eps_diluted_ttm" not in COLUMNS and "roe_q" not in COLUMNS


def test_sql_never_contains_user_text():
    s = Screen(columns=["revenue_ttm"], industry="x'; DROP TABLE y; --", q="Robert'); --",
               sic=["2080"], sic_prefix="20", exchanges=["NYSE"], fye_months=[12],
               filer_categories=["accelerated"],
               filters=[parse_filter("operating_margin_ttm:gte:0.2")], sort="-net_margin_ttm")
    c = compile_screen(s, ["0000000001"])
    for text in ("DROP", "Robert", "2080", "NYSE", "accelerated'", "0.2", "0000000001"):
        assert text not in c.sql
    assert c.params["industry"] == "%x'; DROP TABLE y; --%"
    assert c.params["w0"] == Decimal("0.2")
    # selected columns first, then filter/sort-only ones
    assert c.columns == ["revenue_ttm"]
    assert c.slots == {"revenue_ttm": 0, "operating_margin_ttm": 1, "net_margin_ttm": 2}
    assert "ORDER BY v.v2 DESC NULLS LAST" in c.sql


def test_like_wildcards_are_escaped():
    c = compile_screen(Screen(industry="100%_x"), [])
    assert c.params["industry"] == "%100\\%\\_x%"


@pytest.mark.parametrize("text", ["revenue_ttm:gte", "salary:gt:1", "revenue_ttm:eq:1",
                                  "revenue_ttm:gt:abc", "revenue_ttm:gt:NaN",
                                  "revenue_ttm:gt:Infinity"])
def test_bad_filters(text):
    with pytest.raises(ScreenError):
        parse_filter(text)


def test_bands():
    assert [(f.op, f.value) for f in band_filters("revenue_ttm", "1b-10b")] == \
        [("gte", Decimal("1e9")), ("lt", Decimal("1e10"))]
    assert len(band_filters("revenue_ttm", "gte100b")) == 1


def test_unknown_sort_and_columns():
    with pytest.raises(ScreenError):
        compile_screen(Screen(sort="-salary"), [])
    with pytest.raises(ScreenError):
        compile_screen(Screen(columns=["salary_ttm"]), [])


def test_values_only_for_granted_companies(client, token):
    """alice can read NVDA's financials, not TSLA's: TSLA keeps its metadata, values are null."""
    r = client.get("/v1/screener?columns=revenue_ttm", headers=token("alice"))
    assert r.status_code == 200
    rows = {row["company_id"]: row for row in r.json()["rows"]}
    assert rows[NVDA]["values"] == {"revenue_ttm": "130497000000"}
    assert rows[NVDA]["sources"]["revenue_ttm"]["accession_no"] == "a"
    assert rows[NVDA]["access"] == ["financials"]
    assert rows[TSLA]["values"] == {"revenue_ttm": None}
    assert rows[TSLA]["sources"] == {"revenue_ttm": None}
    assert rows[TSLA]["access"] == []
    assert rows[TSLA]["name"] == "Tesla, Inc."  # identity is public
    # the query itself only reads values of granted companies (and RLS filters again)
    assert client.store.compiled[0].params["granted"] == [NVDA]


def test_access_financials_restricts_universe(client, token):
    client.get("/v1/screener?access=financials", headers=token("alice"))
    c = client.store.compiled[0]
    assert "c.company_id = ANY(%(granted)s)" in c.sql and c.params["granted"] == [NVDA]


def test_unknown_user_gets_metadata_only(client, token):
    rows = client.get("/v1/screener", headers=token("mallory")).json()["rows"]
    assert all(v is None for row in rows for v in row["values"].values())
    assert client.store.compiled[0].params["granted"] == []


def test_filters_and_bands_reach_the_query(client, token):
    r = client.get("/v1/screener?where=operating_margin_ttm:gte:0.25&revenue_band=10b-100b"
                   "&sic_prefix=36&exchange=nasdaq&fiscal_year_end_month=1"
                   "&filer_category=large_accelerated&sort=name", headers=token("bob"))
    assert r.status_code == 200, r.text
    c = client.store.compiled[0]
    assert c.params["sic_prefix"] == "36%" and c.params["exchanges"] == ["NASDAQ"]
    assert c.params["fye_months"] == [1]
    assert [c.params[f"w{i}"] for i in range(3)] == [Decimal("0.25"), Decimal("1e10"),
                                                      Decimal("1e11")]
    assert "ORDER BY b.name ASC" in c.sql


@pytest.mark.parametrize("query", ["columns=salary_ttm", "where=revenue_ttm:gt:x",
                                   "sic=20800", "fiscal_year_end_month=13", "sort=-salary",
                                   "limit=501", "revenue_band=huge", "filer_category=giant"])
def test_bad_requests_are_422(client, token, query):
    assert client.get(f"/v1/screener?{query}", headers=token("bob")).status_code == 422
    assert client.store.compiled == []


def test_csv_export(client, token):
    r = client.get("/v1/screener?columns=revenue_ttm&format=csv&limit=2000",
                   headers=token("alice"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0].endswith(",access,revenue_ttm")
    assert lines[1].startswith(NVDA) and lines[1].endswith(",financials,130497000000")
    assert lines[2].startswith(TSLA) and lines[2].endswith(",,")


def test_fails_closed(token):
    c = TestClient(create_app(FakeAuthorizer(GRANTS, down=True), ScreenStore(), SECRET))
    assert c.get("/v1/screener", headers=token("alice")).status_code == 503


def test_requires_token(client):
    assert client.get("/v1/screener").status_code == 401
