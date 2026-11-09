"""How a figure may be read (migration 035) over real Postgres, as query_api with RLS and no id
list, as with the catalog open: not meaningful, not applicable and old values stay exact but can't
win a sort or pass a filter. Also 036's annual per-share facts and 037's segment check. Skipped
unless TEST_DATABASE_URL is set; every test is rolled back."""

import json
import os
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from query_api.app import create_app
from query_api.authorizer import Access
from query_api.store import Store

from .conftest import SECRET

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

NORMAL, ABSURD, STALE, BANK, NEGATIVE, BLANK = (f"92000000{i:02d}" for i in range(1, 7))
GENUINE, HIGH_DE, LOCKED = "9200000007", "9200000008", "9200000009"
LATEST = date(2026, 9, 30)
USER = "t-readings"
TIERS = frozenset({"financials"})


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class Open:
    """The catalog is open at financials: no id list reaches the query, RLS bounds the values."""

    def can(self, *_):
        return True

    def access(self, _user):
        return Access(TIERS, {})


class Closed:
    """A closed catalog for this user: NORMAL is all it can read."""

    def can(self, _user, _tier, company):
        return company == NORMAL

    def access(self, _user):
        return Access(frozenset(), {NORMAL: {"financials"}})


def reported(conn, cid, metric, end, value, unit="USD"):
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
        " value, unit, value_source, source_tag, accession_no, form, filed, source_accessions,"
        " ingested_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, 'reported', 'us-gaap:X', '0000000000-26-000001', '10-Q',"
        " %s, '{0000000000-26-000001}', now())",
        (cid, metric, end - timedelta(days=91), end, value, unit, end + timedelta(days=40)),
    )


def derived(conn, cid, metric, end, value, inputs, span="ttm", unit="ratio"):
    conn.execute(
        "INSERT INTO gold.derived_financials (company_id, span, metric, period_start, period_end,"
        " value, unit, value_source, inputs, accession_no, form, filed, source_accessions,"
        " ingested_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'computed', %s,"
        " '0000000000-26-000001', '10-Q', %s, '{0000000000-26-000001}', now())",
        (
            cid,
            span,
            metric,
            end - timedelta(days=364),
            end,
            value,
            unit,
            json.dumps(inputs),
            end + timedelta(days=40),
        ),
    )


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as conn, conn.transaction(force_rollback=True):
        for cid, name, sic, industry in (
            (NORMAL, "Normal Co", "3674", "Semiconductors"),
            (ABSURD, "Absurd Co", "7372", "Prepackaged Software"),
            (STALE, "Stale Co", "1311", "Crude Petroleum"),
            (BANK, "Bank Co", "6021", "National Commercial Banks"),
            (NEGATIVE, "Negative Equity Co", "5211", "Retail-Lumber"),
            (BLANK, "Blank Row Co", "6770", "Blank Checks"),
            (GENUINE, "Genuine Co", "1311", "Agricultural Production"),
            (HIGH_DE, "Levered Co", "2844", "Perfumes"),
        ):
            conn.execute(
                "INSERT INTO silver.companies (company_id, name, current_cik, sic,"
                " sic_description) VALUES (%s, %s, %s, %s, %s)",
                (cid, name, cid, sic, industry),
            )
            conn.execute(
                "INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (cid, cid)
            )
            conn.execute(
                "INSERT INTO authz.public_access (company_id, tier) VALUES (%s, 'financials')",
                (cid,),
            )
        conn.execute(
            "INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')", (USER,)
        )
        conn.execute(
            "INSERT INTO silver.companies (company_id, name, current_cik, sic)"
            " VALUES (%s, 'Locked Co', %s, '3674')",
            (LOCKED, LOCKED),
        )
        reported(conn, LOCKED, "revenue", LATEST, 5)
        derived(conn, LOCKED, "gross_margin", LATEST, Decimal("0.9"), {"revenue": 1e9})

        # the anchor: each company's latest period is LATEST
        for cid in (NORMAL, ABSURD, STALE, BANK, NEGATIVE, GENUINE, HIGH_DE):
            reported(conn, cid, "revenue", LATEST, 1_000_000_000)
        rev = {"revenue": 1e9, "gross_profit": 7e8, "operating_income": 2e8}
        derived(conn, NORMAL, "gross_margin", LATEST, Decimal("0.7"), rev)
        derived(conn, NORMAL, "operating_margin", LATEST, Decimal("0.2"), rev)
        derived(
            conn, NORMAL, "net_debt", LATEST, 500_000_000, {"cash": 1}, span="quarter", unit="USD"
        )
        derived(conn, NORMAL, "free_cash_flow", LATEST, 300_000_000, {"capex": 1}, unit="USD")
        derived(
            conn,
            NORMAL,
            "debt_to_equity",
            LATEST,
            Decimal("0.5"),
            {"stockholders_equity": 1e9},
            span="quarter",
        )
        reported(conn, NORMAL, "stockholders_equity", LATEST, 1_000_000_000)
        # Hammer's: $420 of revenue, gross profit $881,279
        derived(
            conn,
            ABSURD,
            "gross_margin",
            LATEST,
            Decimal("2098.283"),
            {"revenue": 420, "gross_profit": 881279},
        )
        derived(
            conn,
            ABSURD,
            "revenue_growth",
            LATEST,
            Decimal("2787.1"),
            {"revenue": 3e6, "revenue_year_ago": 1000},
        )
        # a margin high and real, and growth huge and real (Moderna's)
        derived(conn, GENUINE, "gross_margin", LATEST, Decimal("0.95"), rev)
        derived(conn, GENUINE, "operating_margin", LATEST, Decimal("0.78"), rev)
        derived(
            conn,
            GENUINE,
            "revenue_growth",
            LATEST,
            22,
            {"revenue": 18.5e9, "revenue_year_ago": 803e6},
        )
        # net debt and margin from 2020, with revenue through 2026
        derived(
            conn,
            STALE,
            "net_debt",
            date(2020, 12, 31),
            9_000_000_000,
            {"cash": 1},
            span="quarter",
            unit="USD",
        )
        derived(
            conn,
            STALE,
            "debt_to_equity",
            date(2020, 12, 31),
            Decimal("9"),
            {"stockholders_equity": 1e9},
            span="quarter",
        )
        derived(conn, STALE, "operating_margin", date(2026, 6, 30), Decimal("0.3"), rev)
        # Citi's: a free cash flow exists; JPM's doesn't
        derived(conn, BANK, "free_cash_flow", LATEST, -74_000_000_000, {"capex": 1}, unit="USD")
        derived(conn, BANK, "gross_margin", LATEST, Decimal("0.5"), rev)
        # Lowe's: gold has debt/equity only up to the last positive equity
        derived(
            conn,
            NEGATIVE,
            "debt_to_equity",
            date(2021, 4, 30),
            Decimal("52.2"),
            {"stockholders_equity": 445e6},
            span="quarter",
        )
        reported(conn, NEGATIVE, "stockholders_equity", LATEST, -14_000_000_000)
        # Colgate's: small equity, still a number
        derived(
            conn,
            HIGH_DE,
            "debt_to_equity",
            LATEST,
            Decimal("24.9"),
            {"stockholders_equity": 288e6},
            span="quarter",
        )
        reported(conn, HIGH_DE, "stockholders_equity", LATEST, 288_000_000)
        # has financials, but none of the columns asked for
        reported(conn, BLANK, "total_assets", LATEST, 1_000)
        yield conn


def api(conn, auth=None):
    return TestClient(create_app(auth or Open(), Store(OneConnection(conn)), SECRET))


def screen(conn, token, **params):
    r = api(conn).get("/v1/screener", params=params, headers=token(USER))
    assert r.status_code == 200, r.text
    return r.json()


def names(body):
    return [row["name"] for row in body["rows"]]


def row_of(body, name):
    return next(r for r in body["rows"] if r["name"] == name)


def test_an_absurd_margin_doesnt_win_the_sort_but_keeps_its_figure_and_reason(conn, token):
    body = screen(conn, token, columns=["gross_margin_ttm"], sort="-gross_margin_ttm", limit=50)
    ranked = names(body)
    assert ranked[:2] == ["Genuine Co", "Normal Co"]
    absurd = row_of(body, "Absurd Co")
    assert Decimal(str(absurd["values"]["gross_margin_ttm"])) == Decimal("2098.283")
    assert absurd["readings"]["gross_margin_ttm"]["status"] == "not_meaningful"
    assert "under $100K" in absurd["readings"]["gross_margin_ttm"]["reason"]
    assert ranked.index("Absurd Co") > ranked.index("Normal Co")


def test_it_doesnt_win_ascending_or_pass_a_filter_either(conn, token):
    assert (
        names(screen(conn, token, columns=["gross_margin_ttm"], sort="gross_margin_ttm"))[0]
        == "Normal Co"
    )
    body = screen(
        conn, token, columns=["gross_margin_ttm"], where=["gross_margin_ttm:gte:0.5"], sort="name"
    )
    assert names(body) == ["Genuine Co", "Normal Co"]
    assert body["total"] == 2


def test_growth_from_nothing_is_not_meaningful_and_real_growth_on_a_base_is_kept(conn, token):
    body = screen(conn, token, columns=["revenue_growth_ttm"], sort="-revenue_growth_ttm")
    assert names(body)[0] == "Genuine Co"
    assert "revenue_growth_ttm" not in row_of(body, "Genuine Co")["readings"]
    assert row_of(body, "Absurd Co")["readings"]["revenue_growth_ttm"]["status"] == "not_meaningful"


def test_genuine_highs_stay(conn, token):
    body = screen(
        conn, token, columns=["debt_to_equity_q", "operating_margin_ttm"], sort="-debt_to_equity_q"
    )
    assert names(body)[0] == "Levered Co"
    assert row_of(body, "Levered Co")["readings"] == {}
    assert row_of(body, "Genuine Co")["readings"] == {}


def test_old_values_are_returned_dated_and_marked_and_stay_out_of_sorts_and_filters(conn, token):
    body = screen(conn, token, columns=["net_debt_q"], sort="-net_debt_q")
    assert names(body)[0] == "Normal Co"  # not Stale Co's larger figure from 2020
    stale = row_of(body, "Stale Co")
    assert stale["readings"]["net_debt_q"]["status"] == "old"
    assert stale["sources"]["net_debt_q"]["period_end"] == "2020-12-31"
    assert stale["latest_period"] == LATEST.isoformat()
    assert Decimal(str(stale["values"]["net_debt_q"])) == 9_000_000_000
    assert "Stale Co" not in names(
        screen(
            conn, token, columns=["debt_to_equity_q"], where=["debt_to_equity_q:gte:1"], sort="name"
        )
    )


def test_a_value_merely_behind_says_so_and_still_counts(conn, token):
    body = screen(
        conn,
        token,
        columns=["operating_margin_ttm"],
        where=["operating_margin_ttm:gte:0.25"],
        sort="name",
    )
    assert "Stale Co" in names(body)
    assert row_of(body, "Stale Co")["readings"]["operating_margin_ttm"]["status"] == "behind"


def test_a_bank_reads_not_applicable_with_or_without_a_figure(conn, token):
    body = screen(
        conn,
        token,
        columns=["free_cash_flow_ttm", "net_debt_q", "gross_margin_ttm", "revenue_ttm"],
        q="Bank Co",
        sort="name",
    )
    bank = body["rows"][0]
    assert bank["values"]["net_debt_q"] is None
    for key in ("free_cash_flow_ttm", "net_debt_q", "gross_margin_ttm"):
        assert bank["readings"][key]["status"] == "not_applicable"
        assert "bank" in bank["readings"][key]["reason"]
    assert Decimal(str(bank["values"]["free_cash_flow_ttm"])) == -74_000_000_000  # still reachable
    assert "revenue_ttm" not in bank["readings"]
    assert "net_debt" in bank["not_applicable"]
    # not the first row of a sort on FCF, whichever way
    assert (
        names(screen(conn, token, columns=["free_cash_flow_ttm"], sort="free_cash_flow_ttm"))[0]
        != "Bank Co"
    )


def test_goldman_is_a_bank_by_name_though_the_sec_files_it_under_brokers(conn):
    rows = conn.execute(
        "SELECT gold.not_applicable_metrics('0000886982', '6211'),"
        " gold.not_applicable_metrics('9999999999', '6211')"
    ).fetchone()
    assert "free_cash_flow" in rows[0] and rows[1] == []


# 058: bank holding companies under SICs whose other filers aren't banks (silver.companies' ids)
SCHWAB, AMEX, SYNCHRONY, ALLY = "0000316709", "0000004962", "0001601712", "0000040729"


@pytest.mark.parametrize("cid,sic", [(SCHWAB, "6211"), (AMEX, "6199"), (SYNCHRONY, "6199"),
                                     (ALLY, "6022"), (ALLY, "6199")])
def test_bank_holding_companies_are_banks_by_id(conn, cid, sic):
    banks = conn.execute("SELECT gold.not_applicable_metrics(%s, %s)", (cid, sic)).fetchone()[0]
    assert {"net_debt", "free_cash_flow", "debt_to_equity", "cash_and_investments"} <= set(banks)
    # their SICs alone don't make a bank: another broker or card lender isn't one
    assert conn.execute("SELECT gold.not_applicable_metrics('9999999999', %s)",
                        (sic,)).fetchone()[0] == ([] if sic != "6022" else banks)


def test_one_rule_gives_a_bank_its_readings_page_template_and_peer_columns(conn, token):
    """American Express (SIC 6199) is a bank through gold.not_applicable_metrics alone: the
    screener's `not_applicable` (the web's bank template reads it) and the peers' bank columns."""
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic,"
                 " sic_description) VALUES (%s, 'American Express', %s, '6199',"
                 " 'Finance Services')", (AMEX, AMEX))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (AMEX, AMEX))
    conn.execute("INSERT INTO authz.public_access (company_id, tier) VALUES (%s, 'financials')",
                 (AMEX,))
    reported(conn, AMEX, "revenue", LATEST, 70_000_000_000)
    derived(conn, AMEX, "free_cash_flow", LATEST, 14_000_000_000, {"capex": 1}, unit="USD")
    body = api(conn).get("/v1/screener", params={"columns": ["free_cash_flow_ttm"],
                                                 "company_id": [AMEX]},
                         headers=token(USER)).json()
    (row,) = body["rows"]
    assert "net_debt" in row["not_applicable"]
    assert row["readings"]["free_cash_flow_ttm"]["status"] == "not_applicable"
    peers = api(conn).get(f"/v1/companies/{AMEX}/peers", headers=token(USER))
    assert peers.status_code == 200, peers.text
    keys = [c["key"] for c in peers.json()["columns"]]
    assert keys[:3] == ["revenue_ttm", "net_interest_income_ttm", "efficiency_ratio_ttm"]


def test_negative_equity_is_said_outright_not_read_from_an_old_quarter(conn, token):
    body = screen(conn, token, columns=["debt_to_equity_q"], sort="-debt_to_equity_q")
    negative = row_of(body, "Negative Equity Co")
    assert negative["readings"]["debt_to_equity_q"]["status"] == "not_meaningful"
    assert "negative" in negative["readings"]["debt_to_equity_q"]["reason"]
    assert names(body).index("Negative Equity Co") > names(body).index("Normal Co")


def test_a_row_of_blanks_isnt_counted_until_asked_for_by_name(conn, token):
    body = screen(conn, token, columns=["gross_margin_ttm"], sort="name")
    assert "Blank Row Co" not in names(body)
    assert body["total"] == len(names(body))
    assert names(screen(conn, token, columns=["total_assets_q"], sort="name"))[0] == "Blank Row Co"
    assert (
        names(screen(conn, token, columns=["gross_margin_ttm"], q="Blank Row"))[0] == "Blank Row Co"
    )


def test_the_closed_catalog_still_lists_what_it_cant_read_and_reads_nothing_of_it(conn, token):
    r = api(conn, Closed()).get(
        "/v1/screener", params={"columns": ["gross_margin_ttm"], "q": "Locked"}, headers=token(USER)
    )
    locked = r.json()["rows"][0]
    assert locked["values"]["gross_margin_ttm"] is None and locked["readings"] == {}
    assert locked["latest_period"] is None
    r = api(conn, Closed()).get(
        "/v1/screener",
        params={"columns": ["gross_margin_ttm"], "sort": "name"},
        headers=token(USER),
    )
    listed = [row["name"] for row in r.json()["rows"]]
    assert "Locked Co" in listed and "Normal Co" in listed


def test_rls_alone_gives_no_readings_or_dates(conn, token):
    """Open says the catalog is public, but this user has no rows: RLS must still hold."""
    r = api(conn).get(
        "/v1/screener",
        params={"columns": ["gross_margin_ttm", "net_debt_q"], "q": "Stale"},
        headers=token("t-nobody"),
    )
    row = r.json()["rows"][0]
    assert row["sources"]["net_debt_q"] is None and row["values"]["net_debt_q"] is None
    assert row["latest_period"] is None
    assert row["readings"] == {}


def test_industry_sorts_by_the_name_shown_not_the_code(conn, token):
    body = screen(conn, token, columns=["revenue_ttm"], sort="sic")
    industries = [r["industry"] for r in body["rows"] if r["industry"]]
    # Agricultural Production's SIC here is 1311, Semiconductors' 3674: a code sort reads otherwise
    assert industries == sorted(industries, key=str.lower)
    assert industries[0] == "Agricultural Production"


def test_sectors_read_the_same_joined_for_the_page_or_for_every_company(conn, token):
    """Pre-ship review: 046's sector is joined after the LIMIT unless a filter or sort needs it;
    the rows say the same either way."""
    every = {r["name"]: (r["sector"], r["sector_industry"])
             for r in screen(conn, token, columns=["revenue_ttm"], sort="name", limit=50)["rows"]}
    assert every["Normal Co"] == ("Technology", "Semiconductors")
    assert every["Bank Co"] == ("Financials", "Banks & thrifts")
    tech = screen(conn, token, columns=["revenue_ttm"], sector=["Technology"], sort="name")
    assert [r["name"] for r in tech["rows"]] == ["Absurd Co", "Normal Co"]
    assert all(every[r["name"]] == (r["sector"], r["sector_industry"]) for r in tech["rows"])
    grouped = screen(conn, token, columns=["revenue_ttm"], sort="sector", limit=50)["rows"]
    keys = [f"{r['sector']} / {r['sector_industry']}".lower() for r in grouped if r["sector"]]
    assert keys == sorted(keys) and len(keys) == sum(1 for v in every.values() if v[0])


def test_financials_reads_not_meaningful_only_when_asked(conn, token):
    c = api(conn)
    plain = c.get(
        f"/v1/companies/{ABSURD}/financials",
        headers=token(USER),
        params={"metrics": ["gross_margin"], "view": "ttm"},
    ).json()
    assert "not_meaningful" not in plain["rows"][0]  # the agent's tool shape
    asked = c.get(
        f"/v1/companies/{ABSURD}/financials",
        headers=token(USER),
        params={"metrics": ["gross_margin"], "view": "ttm", "readings": "true"},
    ).json()
    assert "under $100K" in asked["rows"][0]["not_meaningful"]


def test_financials_say_where_equity_makes_a_ratio_meaningless(conn, token):
    """The web's Ratios table read negative equity itself (equity <= 0 means n/m): the API says
    it now, by 035's rule, both for a period gold has no ratio row for (014's guard) and over a
    row's own reading. Only with readings: the agent's tool shape doesn't change."""
    c = api(conn)
    q = {"metrics": ["debt_to_equity", "roe", "stockholders_equity"], "quarters": 8}
    plain = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER), params=q).json()
    assert "not_meaningful_periods" not in plain
    asked = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER),
                  params={**q, "readings": "true"}).json()
    assert asked["not_meaningful_periods"] == [
        {"metric": "debt_to_equity", "period_end": "2026-09-30",
         "reason": "stockholders' equity is negative"},
        {"metric": "roe", "period_end": "2026-09-30", "reason": "stockholders' equity is negative"}]
    # from the company's equity periods, not the rows': debt_to_equity alone has no row there
    alone = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER),
                  params={"metrics": ["debt_to_equity"], "quarters": 8, "readings": "true"}).json()
    assert [r["period_end"] for r in alone["rows"]] == ["2021-04-30"]
    assert alone["not_meaningful_periods"] == [
        {"metric": "debt_to_equity", "period_end": "2026-09-30",
         "reason": "stockholders' equity is negative"}]
    # still behind require() and RLS: a user who can't read the company gets the 403, nothing else
    denied = api(conn, Closed()).get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER),
                                     params={"metrics": ["debt_to_equity"], "readings": "true"})
    assert denied.status_code == 403 and "not_meaningful_periods" not in denied.text
    # and with every check passing (an allow-all authorizer), RLS alone reads no equity for them
    nobody = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token("t-nobody"),
                   params={"metrics": ["debt_to_equity"], "readings": "true"}).json()
    assert nobody["rows"] == [] and nobody["not_meaningful_periods"] == []
    # a ratio row at a negative-equity date reads n/m for that reason, whatever its own value
    derived(conn, NEGATIVE, "debt_to_equity", LATEST, Decimal("3.1"), {}, span="quarter")
    asked = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER),
                  params={**q, "readings": "true"}).json()
    de = [r for r in asked["rows"] if r["metric"] == "debt_to_equity"]
    assert de[0]["period_end"] == "2026-09-30" and de[0]["value"] == "3.1"
    assert de[0]["not_meaningful"] == "stockholders' equity is negative"
    assert [p["metric"] for p in asked["not_meaningful_periods"]] == ["roe"]
    # the CSV is the figures as they are
    csv = c.get(f"/v1/companies/{NEGATIVE}/financials", headers=token(USER),
                params={**q, "readings": "true", "format": "csv"})
    assert csv.status_code == 200 and "not_meaningful" not in csv.text


@pytest.mark.parametrize(
    ("metric", "value", "inputs", "equity", "says"),
    [
        ("gross_margin", "1.0", {"revenue": 1e9}, None, None),  # no cost of revenue: real
        ("gross_margin", "1.047", {"revenue": 4e9}, None, "larger than revenue"),  # NiSource
        ("gross_margin", "-0.5", {"revenue": 1e9}, None, None),
        ("gross_margin", "0.5", {"revenue": 420}, None, "under $100K"),
        ("operating_margin", "0.778", {"revenue": 7e9}, None, None),  # AppLovin
        ("operating_margin", "1.118", {"revenue": 114e6}, None, "larger than revenue"),
        ("operating_margin", "-3.5", {"revenue": 8e8}, None, None),  # Lucid
        ("operating_margin", "-52", {"revenue": 1e7}, None, "ten times revenue"),
        ("net_margin", "1.36", {"revenue": 664e6}, None, None),  # TriMas, a sale
        ("net_margin", "11.6", {"revenue": 72e6}, None, "ten times revenue"),
        ("fcf_margin", "-11", {"revenue": 1e7}, None, "ten times revenue"),
        ("revenue_growth", "22", {"revenue_year_ago": 803e6}, None, None),  # Moderna
        ("revenue_growth", "22.3", {"revenue_year_ago": 19e6}, None, None),  # Liquidia
        ("revenue_growth", "22.7", {"revenue_year_ago": 4.9e6}, None, "under $10M"),
        ("revenue_growth", "0.5", {"revenue_year_ago": 5e4}, None, "under $100K"),
        ("revenue_growth", "-1.4", {"revenue_year_ago": 5e8}, None, "negative"),
        ("revenue_growth", "-1", {"revenue_year_ago": 5e8}, None, None),  # zero revenue now: a fact
        ("net_income_growth", "-3", {"net_income_year_ago": 5e8}, None, "profit to a loss"),
        ("eps_growth", "12", {}, None, "very small base"),
        ("debt_to_equity", "24.9", {}, Decimal(288e6), None),  # Colgate
        ("debt_to_equity", "99.2", {}, Decimal(12e6), None),  # JELD-WEN, just under
        ("debt_to_equity", "563", {}, Decimal(7e6), "over 100 times"),  # GoDaddy
        ("debt_to_equity", "-3", {}, Decimal(1e6), "below zero"),
        ("debt_to_equity", "52.2", {}, Decimal(-14e9), "negative"),  # Lowe's
        ("debt_to_equity", None, None, Decimal(-14e9), "negative"),
        ("roe", "1.5", {}, Decimal(1e9), None),
        ("roe", "374", {}, Decimal(7e8), "ten times equity"),  # Nutanix
        ("payout_ratio", "11", {}, None, "ten times"),
        ("current_ratio", "2951", {}, None, "close to zero"),  # a currency trust
        ("current_ratio", "46", {}, None, None),
        ("revenue", "100", None, None, None),
    ],
)
def test_the_rule_on_real_shapes(conn, metric, value, inputs, equity, says):
    got = conn.execute(
        "SELECT gold.not_meaningful(%s, %s::numeric, %s::jsonb, %s::numeric)",
        (metric, value, json.dumps(inputs) if inputs is not None else None, equity),
    ).fetchone()[0]
    if says is None:
        assert got is None
    else:
        assert says in got


@pytest.mark.parametrize(
    ("days", "status"),
    [
        (0, None),
        (45, None),
        (46, "behind"),
        (91, "behind"),
        (275, "behind"),
        (300, "behind"),
        (301, "old"),
        (3000, "old"),
    ],
)
def test_stale_is_defined_once(conn, days, status):
    got = conn.execute(
        "SELECT gold.period_status(%s::date - %s::int, %s::date)", (LATEST, days, LATEST)
    ).fetchone()[0]
    assert got == status


def test_an_impossible_future_date_doesnt_make_everything_else_old(conn, token):
    reported(conn, NORMAL, "net_income", date(2033, 3, 31), 1)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', %s, true)", (USER,))
        cur.execute("SELECT latest FROM gold.latest_period(%s)", (NORMAL,))
        assert cur.fetchone()[0] == LATEST
        cur.execute("RESET ROLE")


EURO = "9200000010"


def euro_co(conn):
    """Birkenstock's shape (migration 056): figures in euros, only fiscal years and balances."""
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic,"
                 " sic_description) VALUES (%s, 'Euro Co', %s, '3021', 'Footwear')", (EURO, EURO))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (EURO, EURO))
    conn.execute("INSERT INTO authz.public_access (company_id, tier) VALUES (%s, 'financials')",
                 (EURO,))
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
        " value, unit, value_source, source_tag, accession_no, form, filed, source_accessions,"
        " ingested_at) VALUES (%s, 'total_assets', NULL, %s, 4942120000, 'EUR', 'reported',"
        " 'ifrs-full:Assets', '0001977102-25-000001', '20-F', %s, '{0001977102-25-000001}', now())",
        (EURO, LATEST, LATEST + timedelta(days=60)))
    derived(conn, EURO, "revenue", LATEST, 9_000_000_000_000, {"quarters": []}, unit="EUR")
    derived(conn, EURO, "gross_margin", LATEST, Decimal("0.99"), {"revenue": 2e9})


def test_an_amount_in_another_currency_is_shown_in_it_but_never_ranked_with_dollars(conn, token):
    euro_co(conn)
    body = screen(conn, token, columns=["revenue_ttm", "gross_margin_ttm"], sort="-revenue_ttm",
                  limit=50)
    assert names(body)[0] != "Euro Co"  # 9 trillion euros doesn't top a dollar sort
    euro = row_of(body, "Euro Co")
    assert Decimal(str(euro["values"]["revenue_ttm"])) == 9_000_000_000_000
    reading = euro["readings"]["revenue_ttm"]
    assert reading["status"] == "other_currency" and reading["unit"] == "EUR"
    assert reading["reason"].startswith("reported in EUR")
    # a ratio has no currency: it ranks with everyone's
    assert "gross_margin_ttm" not in euro["readings"]
    assert names(screen(conn, token, columns=["gross_margin_ttm"], sort="-gross_margin_ttm",
                        limit=50))[0] == "Euro Co"
    # nor does a filter take it
    over = screen(conn, token, columns=["revenue_ttm"], where=["revenue_ttm:gt:1"], sort="name",
                  limit=50)
    assert "Euro Co" not in names(over)
    # a dollar company's readings say nothing new
    assert "revenue_ttm" not in row_of(body, "Normal Co")["readings"]


def test_financials_say_their_currency_and_that_they_are_years(conn, token):
    euro_co(conn)
    c = api(conn)
    body = c.get(f"/v1/companies/{EURO}/financials", headers=token(USER),
                 params={"view": "ttm", "metrics": ["revenue"]}).json()
    assert (body["currency"], body["cadence"]) == ("EUR", "annual")
    usd = c.get(f"/v1/companies/{NORMAL}/financials", headers=token(USER),
                params={"view": "ttm", "metrics": ["revenue"]}).json()
    assert "currency" not in usd and "cadence" not in usd  # a dollar filer's JSON is as before


EIDP_LIKE, SIX_K = "9200000021", "9200000022"


def periodic(conn, cid, form, filed, n):
    conn.execute("INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date,"
                 " report_date) VALUES (%s, %s, %s, %s, %s)",
                 (f"{cid}-26-{n:06d}", cid, form, filed, filed - timedelta(days=40)))


def listed(conn, cid, name):
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic)"
                 " VALUES (%s, %s, %s, '2800')", (cid, name, cid))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (cid, cid))
    for tier in ("financials", "filings"):
        conn.execute("INSERT INTO authz.public_access (company_id, tier) VALUES (%s, %s)",
                     (cid, tier))


def test_a_us_filer_whose_figures_stopped_is_old_not_once_a_year(conn, token):
    """B1 (pre-ship review): EIDP files 10-Qs but its figures end in 2019. The old rule (no
    quarter in 800 days, TTM rows) called it a 20-F/40-F filer; ~96 US filers on hermes."""
    listed(conn, EIDP_LIKE, "Stale US Co")
    reported(conn, EIDP_LIKE, "revenue", date(2019, 3, 31), 5_000_000_000)
    derived(conn, EIDP_LIKE, "revenue", date(2019, 3, 31), 21_000_000_000, {"quarters": []},
            unit="USD")
    periodic(conn, EIDP_LIKE, "10-K", date(2026, 2, 10), 1)
    periodic(conn, EIDP_LIKE, "10-Q", date(2026, 7, 31), 2)
    # a 20-F filer whose quarters come in gold too (ICON, Arm: 6-Ks): quarters
    listed(conn, SIX_K, "Six-K Co")
    recent = date.today() - timedelta(days=120)
    reported(conn, SIX_K, "revenue", recent, 2_000_000_000)
    derived(conn, SIX_K, "revenue", recent, 8_000_000_000, {"quarters": []}, unit="USD")
    periodic(conn, SIX_K, "20-F", date.today() - timedelta(days=200), 1)
    # Birkenstock's, now with its 20-F in the index: once a year
    euro_co(conn)
    periodic(conn, EURO, "20-F", LATEST + timedelta(days=60), 1)
    c = api(conn)
    for cid, cadence in ((EIDP_LIKE, None), (SIX_K, None), (EURO, "annual")):
        body = c.get(f"/v1/companies/{cid}/financials", headers=token(USER),
                     params={"view": "ttm", "metrics": ["revenue"]}).json()
        assert body.get("cadence") == cadence, cid
    store = Store(OneConnection(conn))
    assert store.cadence(USER, EIDP_LIKE) == "quarterly"
    assert store.cadence(USER, SIX_K) == "quarterly"
    assert store.cadence(USER, EURO) == "annual"
    # a 20-F filer who later files 10-Qs is quarterly again; one who files 20-Fs after its
    # 10-Qs is annual (the latest report decides)
    periodic(conn, EURO, "10-Q", LATEST + timedelta(days=120), 2)
    assert store.cadence(USER, EURO) == "quarterly"
    periodic(conn, EIDP_LIKE, "20-F", date(2026, 10, 1), 3)
    assert store.cadence(USER, EIDP_LIKE) == "annual"


@pytest.mark.parametrize(
    ("metric", "value", "inputs", "currency", "says"),
    [
        # the dollar bases (035) only for dollar amounts; every ratio bound for every currency
        ("gross_margin", "0.4", {"revenue": 5e4}, "USD", "under $100K"),
        ("gross_margin", "0.4", {"revenue": 5e4}, "JPY", None),  # ¥50,000 is $330
        ("gross_margin", "0.4", {"revenue": 5e4}, None, "under $100K"),  # unknown: dollars
        ("gross_margin", "1.2", {"revenue": 5e4}, "JPY", "larger than revenue"),
        ("operating_margin", "12", {"revenue": 5e4}, "KRW", "larger than revenue"),
        ("net_margin", "-11", {"revenue": 5e10}, "JPY", "ten times revenue"),
        ("revenue_growth", "22.7", {"revenue_year_ago": 4.9e6}, "USD", "under $10M"),
        ("revenue_growth", "22.7", {"revenue_year_ago": 4.9e6}, "JPY", None),
        ("revenue_growth", "0.5", {"revenue_year_ago": 5e4}, "EUR", None),
        ("revenue_growth", "-1.4", {"revenue_year_ago": 5e8}, "JPY", "negative"),
        ("net_income_growth", "15", {"net_income_year_ago": 9e6}, "KRW", None),
        ("net_income_growth", "-3", {"net_income_year_ago": 5e8}, "KRW", "profit to a loss"),
        ("debt_to_equity", "563", {}, "JPY", "over 100 times"),
    ],
)
def test_the_dollar_bases_apply_only_to_dollars(conn, metric, value, inputs, currency, says):
    """057: 035's absolute bases are in dollars; against yen or won they never fired (¥100K is
    $650), so they apply only to dollar amounts and the ratio bounds to every currency. The
    four-argument form (an older Query API) reads as dollars, as before."""
    got = conn.execute(
        "SELECT gold.not_meaningful(%s, %s::numeric, %s::jsonb, NULL, %s)",
        (metric, value, json.dumps(inputs), currency),
    ).fetchone()[0]
    if says is None:
        assert got is None
    else:
        assert says in got
    if currency in (None, "USD"):
        four = conn.execute("SELECT gold.not_meaningful(%s, %s::numeric, %s::jsonb)",
                            (metric, value, json.dumps(inputs))).fetchone()[0]
        assert four == got


YEN = "9200000011"


def yen_co(conn, revenue):
    """Toyota's shape after 056: every amount in yen, here a ¥50,000 revenue (a small base in
    dollars it isn't) with a 40% margin and +2,270% growth from ¥4.9M."""
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic,"
                 " sic_description) VALUES (%s, 'Yen Co', %s, '3711', 'Motor Vehicles')",
                 (YEN, YEN))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (YEN, YEN))
    conn.execute("INSERT INTO authz.public_access (company_id, tier) VALUES (%s, 'financials')",
                 (YEN,))
    reported(conn, YEN, "revenue", LATEST, revenue, unit="JPY")
    derived(conn, YEN, "gross_margin", LATEST, Decimal("0.4"), {"revenue": revenue})
    derived(conn, YEN, "revenue_growth", LATEST, Decimal("22.7"), {"revenue_year_ago": 4.9e6})


def test_a_yen_companys_small_base_is_not_a_dollar_one(conn, token):
    """057 through every reader: the screener's reading, /financials with readings and a chart's
    point read a yen company's margin and growth as figures; a dollar company with the same
    numbers reads n/m, as before."""
    yen_co(conn, 50_000)
    reported(conn, BLANK, "revenue", LATEST, 50_000)
    derived(conn, BLANK, "gross_margin", LATEST, Decimal("0.4"), {"revenue": 50_000})
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', %s, true)", (USER,))
        for cid, expect in ((YEN, None), (BLANK, "not_meaningful")):
            cur.execute("SELECT x.status FROM gold.latest_period(%s) lp, gold.latest_reading("
                        "%s, 'ttm', 'gross_margin', NULL, lp.latest) x", (cid, cid))
            assert cur.fetchone()[0] == expect, cid
        cur.execute("SELECT gold.amount_currency(%s), gold.amount_currency(%s)", (YEN, BLANK))
        assert cur.fetchone() == ("JPY", "USD")
        cur.execute("RESET ROLE")
    c = api(conn)
    rows = c.get(f"/v1/companies/{YEN}/financials", headers=token(USER),
                 params={"view": "ttm", "metrics": ["gross_margin", "revenue_growth"],
                         "readings": "true"}).json()["rows"]
    assert [r["not_meaningful"] for r in rows] == [None, None]
    usd = c.get(f"/v1/companies/{BLANK}/financials", headers=token(USER),
                params={"view": "ttm", "metrics": ["gross_margin"], "readings": "true"}).json()
    assert "under $100K" in usd["rows"][0]["not_meaningful"]
    point = c.post("/v1/formulas/evaluate", headers=token(USER),
                   json={"formula": "gross_margin", "view": "ttm", "default_company": YEN}).json()
    assert point["points"] and "not_meaningful" not in point["points"][-1]


def test_an_annual_only_yen_filers_currency_is_yen(conn, token):
    """060: a 20-F filer reports no quarters; its revenue and net income are stated years, kept
    only as TTM rows of gold.derived_financials. 057's gold.amount_currency and latest_reading's
    base lookup read quarterly rows alone, so such a yen filer read USD and its ¥50,000 base got
    the dollar rule ("revenue is under $100K"). Both now read the TTM rows."""
    conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, sic,"
                 " sic_description) VALUES (%s, 'Yen 20-F Co', %s, '3711', 'Motor Vehicles')",
                 (YEN, YEN))
    conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)", (YEN, YEN))
    conn.execute("INSERT INTO authz.public_access (company_id, tier) VALUES (%s, 'financials')",
                 (YEN,))
    reported(conn, YEN, "total_assets", LATEST, 9_000_000, unit="JPY")  # no quarterly P&L
    derived(conn, YEN, "revenue", LATEST, 50_000, None, unit="JPY")
    derived(conn, YEN, "net_income", LATEST, 20_000, None, unit="JPY")
    derived(conn, YEN, "gross_margin", LATEST, Decimal("0.4"), {"revenue": 50_000})
    derived(conn, YEN, "net_income_growth", LATEST, Decimal("22.7"),
            {"net_income_year_ago": 4.9e6 / 5})
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        cur.execute("SELECT set_config('app.user_id', %s, true)", (USER,))
        cur.execute("SELECT gold.amount_currency(%s)", (YEN,))
        assert cur.fetchone() == ("JPY",)  # 057: USD
        for metric in ("gross_margin", "net_income_growth"):
            cur.execute("SELECT x.status, x.reason FROM gold.latest_period(%s) lp,"
                        " gold.latest_reading(%s, 'ttm', %s, NULL, lp.latest) x",
                        (YEN, YEN, metric))
            assert cur.fetchone() == (None, None), metric  # 057: not_meaningful, in dollars
        cur.execute("RESET ROLE")
    rows = api(conn).get(f"/v1/companies/{YEN}/financials", headers=token(USER),
                         params={"view": "ttm", "metrics": ["revenue", "gross_margin"],
                                 "readings": "true"}).json()
    assert rows["currency"] == "JPY"
    assert {r["metric"]: r["not_meaningful"] for r in rows["rows"]} == {
        "revenue": None, "gross_margin": None}


def test_latest_reading_stays_inlinable(conn):
    """The screener's plan depends on it, like 032's gold.latest_value (no SET, not DEFINER, a
    single SQL statement, not volatile)."""
    rows = conn.execute(
        "SELECT p.proname, p.prosecdef, p.provolatile, p.proconfig, l.lanname FROM pg_proc p"
        " JOIN pg_language l ON l.oid = p.prolang"
        " WHERE p.pronamespace = 'gold'::regnamespace"
        " AND p.proname IN ('latest_reading', 'latest_period')"
    ).fetchall()
    assert len(rows) == 2
    for name, definer, volatility, config, language in rows:
        assert (definer, config, language) == (False, None, "sql"), name
        assert volatility == "s", name


def annual_fact(
    conn,
    cid,
    metric,
    end,
    value,
    accession,
    filed,
    form="10-K",
    tag="EarningsPerShareDiluted",
    unit="USD/shares",
):
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (cid, metric, tag, unit, end - timedelta(days=364), end, value, accession, form, filed),
    )


def test_annual_per_share_reads_the_latest_10k_never_a_proxy_and_respects_the_read_rule(
    conn, token
):
    end = date(2025, 12, 31)
    annual_fact(
        conn, NORMAL, "eps_diluted", end, Decimal("49.0"), "0000000000-26-000010", date(2026, 2, 1)
    )
    # the next year's 10-K restates it for a split
    annual_fact(
        conn, NORMAL, "eps_diluted", end, Decimal("4.90"), "0000000000-27-000010", date(2027, 2, 1)
    )
    annual_fact(
        conn,
        NORMAL,
        "eps_diluted",
        end,
        Decimal("99"),
        "0000000000-27-000011",
        date(2027, 4, 1),
        form="DEF 14A",
    )
    r = api(conn).get(f"/v1/companies/{NORMAL}/annual-per-share", headers=token(USER))
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert [(x["metric"], x["period_end"]) for x in rows] == [("eps_diluted", "2025-12-31")]
    assert Decimal(str(rows[0]["value"])) == Decimal("4.90")
    assert rows[0]["citation"]["accession_no"] == "0000000000-27-000010"
    assert rows[0]["value_source"] == "reported"
    # layer 2 alone: the allow-all authorizer lets this through, the function's rule doesn't
    assert (
        api(conn)
        .get(f"/v1/companies/{NORMAL}/annual-per-share", headers=token("t-nobody"))
        .json()["rows"]
        == []
    )
    # and query_api still can't read a value of the facts table itself
    with conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE query_api")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            cur.execute("SELECT value FROM silver.financial_facts LIMIT 1")
        cur.execute("RESET ROLE")


def test_annual_per_share_needs_the_tier_first(conn, token):
    r = api(conn, Closed()).get(f"/v1/companies/{STALE}/annual-per-share", headers=token(USER))
    assert r.status_code == 403


def segment(conn, cid, axis, member, value, end, metric="operating_income"):
    conn.execute(
        "INSERT INTO gold.segment_financials (company_id, span, axis, member, member_label,"
        " metric, period_start, period_end, value, unit, value_source, source_tag, axis_qname,"
        " accession_no, filer_cik, form, filed, source_accessions, ingested_at)"
        " VALUES (%s, 'quarter', %s, %s, %s,"
        " %s, %s, %s, %s, 'USD', 'reported', 'x', 'y', '0000000000-26-000001', %s, '10-Q', %s,"
        " '{0000000000-26-000001}', now())",
        (cid, axis, member, member, metric, end - timedelta(days=91), end, value, cid, end),
    )


def test_a_reconciliation_member_that_restates_the_segments_sum_isnt_added_again(conn):
    """NVIDIA's: Corporate $66,595M is Compute and Networking + Graphics, against $63,734M. The
    check said 109.0% more; the app's own figures say 4.5%."""
    end = date(2026, 7, 26)
    reported(conn, NORMAL, "operating_income", end, 63_734)
    segment(conn, NORMAL, "business_segment", "Compute", 62_696, end)
    segment(conn, NORMAL, "business_segment", "Graphics", 3_899, end)
    segment(conn, NORMAL, "reconciliation", "Corporate", 66_595, end)
    conn.execute("SELECT ops.check_segments(%s)", ([NORMAL],))
    detail = conn.execute(
        "SELECT detail FROM ops.quality_findings WHERE company_id = %s"
        " AND check_name = 'segment_sum_business_segment'",
        (NORMAL,),
    ).fetchone()[0]
    assert float(detail["deviation"]) == pytest.approx(0.0449, abs=1e-4)
    assert detail["members"] == 2


def test_a_real_reconciling_item_still_counts(conn):
    end = date(2026, 7, 26)
    reported(conn, STALE, "operating_income", end, 100)
    segment(conn, STALE, "business_segment", "A", 70, end)
    segment(conn, STALE, "business_segment", "B", 50, end)
    segment(conn, STALE, "reconciliation", "Corporate", -20, end)
    conn.execute("SELECT ops.check_segments(%s)", ([STALE],))
    assert (
        conn.execute(
            "SELECT count(*) FROM ops.quality_findings WHERE company_id = %s"
            " AND check_name LIKE 'segment%%'",
            (STALE,),
        ).fetchone()[0]
        == 0
    )


def test_the_screener_files_a_first_week_year_end_under_the_month_before(conn, token):
    """J&J's 0103 is a December year, Target's 0201 a January one, NVIDIA's 0131 stays January: the
    filter and the sort read silver.fiscal_year_end_month (migration 042). What's stored, and what
    the API returns, stay the SEC's MMDD."""
    for cid, fye in ((NORMAL, "0103"), (ABSURD, "0201"), (GENUINE, "0131"), (HIGH_DE, "1231")):
        conn.execute("UPDATE silver.companies SET fiscal_year_end = %s WHERE company_id = %s",
                     (fye, cid))
    conn.execute("UPDATE silver.companies SET fiscal_year_end = '0630' WHERE company_id = ANY(%s)",
                 ([STALE, BANK, NEGATIVE, BLANK],))

    def fyes(**params):
        params = {"columns": ["revenue_ttm"], "sort": "name", **params}
        r = api(conn).get("/v1/screener", params=params, headers=token(USER))
        assert r.status_code == 200, r.text
        return [row["fiscal_year_end"] for row in r.json()["rows"]]

    assert sorted(fyes(fiscal_year_end_month=12)) == ["0103", "1231"]
    assert sorted(fyes(fiscal_year_end_month=1)) == ["0131", "0201"]
    assert fyes(sort="fiscal_year_end")[-2:] in (["0103", "1231"], ["1231", "0103"])
