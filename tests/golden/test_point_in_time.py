"""Point in time (B1, migrations 085-087) on real companies: the reference set's companyfacts
payloads (fixtures/, test_reference_set.py's) through bronze -> silver -> gold, then every company's
history replayed (gold.rebuild_known), and read back as of past days.

- No look-ahead: as of every filing date D and the day before it, no figure, citation or input
  filing in the history was filed after D, and the history equals gold's own pipeline run over the
  facts filed by D (gold.financial_rows), row for row; the open versions equal gold today.
- Original vs restated: CrowdStrike's Q4 FY2025 net income as first filed (-92,282,000, 10-K of
  2025-03-10) until the FY2026 10-K restated it (-88,252,000, 2026-03-05), each citing its filing.
- The incremental path: a later filing's facts change only the history from its date on.
- gold.financials_at(D) is the history believed at D; the single-company read
  (gold.company_financials_at) equals it, and with the history missing replays at read time to the
  same values; neither reads for a user without access.
- The three kinds of restatement: a split (Booking's 25-for-1), another line (Plexus' revenue read
  from another contract-revenue tag), the same line (CrowdStrike's restated net income).
- The Query API as of a day: /financials (and `originals`), the screener equal to the company
  page, /per-share applying only the splits restated by then, /restatements, and today's screen
  after an as-of one.

Skipped unless TEST_DATABASE_URL is set; rolled back."""

import csv
import gzip
import os
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="TEST_DATABASE_URL not set")

HERE = Path(__file__).parent
TICKERS = {"CRWD", "BKNG", "PLXS", "ZION"}  # a split, another line, the same line, a bank
COMPANIES = [c for c in csv.DictReader((HERE / "reference_companies.csv").open(newline=""))
             if c["ticker"] in TICKERS]
IDS = {c["ticker"]: c["company_id"] for c in COMPANIES}
USER = "point-in-time"
KEY = "span, metric, period_end, period_start, value, unit, value_source, source_tag"


@pytest.fixture(scope="module")
def gold():
    from facts_fetcher.db import refresh_gold, refresh_silver, store_payload

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn, \
            conn.transaction(force_rollback=True):
        conn.execute("TRUNCATE gold.quarterly_financials, gold.derived_financials,"
                     " gold.segment_financials, ops.quality_findings, ops.quality_companies,"
                     " silver.financial_facts, bronze.company_facts, silver.company_aliases,"
                     " silver.company_ciks, silver.companies, gold.financials_known,"
                     " gold.known_companies")
        conn.execute("DELETE FROM ops.company_builds")
        for c in COMPANIES:
            ciks = c["ciks"].split()
            conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked,"
                         " sic) VALUES (%s, %s, %s, true, %s)",
                         (c["company_id"], c["name"], ciks[-1], c["sic"] or None))
            for cik in ciks:
                conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                             (cik, c["company_id"]))
                store_payload(conn, cik, gzip.decompress(
                    (HERE / "fixtures" / f"CIK{cik}.json.gz").read_bytes()))
            refresh_silver(conn, ciks)
            conn.execute("INSERT INTO authz.public_access (company_id, tier)"
                         " VALUES (%s, 'financials') ON CONFLICT DO NOTHING", (c["company_id"],))
        conn.execute("INSERT INTO authz.user_tiers (user_id, tier) VALUES (%s, 'financials')"
                     " ON CONFLICT DO NOTHING", (USER,))
        ids = sorted(IDS.values())
        refresh_gold(conn, ids)
        conn.execute("SELECT gold.rebuild_known(%s)", (ids,))
        yield conn


def dates(conn, company: str) -> list[date]:
    return [r[0] for r in conn.execute(
        "SELECT filed FROM gold.knowledge_facts(%s) ORDER BY 1", (company,))]


def test_no_look_ahead_and_the_history_is_the_pipeline_at_every_date(gold):
    checked = 0
    for ticker, company in sorted(IDS.items()):
        days = dates(gold, company)
        assert len(days) >= 5, ticker
        for d in sorted({*days, *(x - timedelta(days=1) for x in days)}):
            late = gold.execute(
                """
                SELECT count(*) FROM gold.financials_known f
                WHERE f.company_id = %(c)s AND f.known_from <= %(d)s
                  AND (f.known_to IS NULL OR f.known_to > %(d)s)
                  AND (f.filed > %(d)s
                       OR EXISTS (SELECT 1 FROM unnest(f.source_accessions) a
                                  JOIN silver.financial_facts s ON s.accession_no = a
                                  WHERE s.filed > %(d)s))
                """, {"d": d, "c": company}).fetchone()[0]
            assert late == 0, (ticker, d)
            diff = gold.execute(
                f"""
                WITH h AS (SELECT {KEY}, gold.input_values(inputs) AS iv
                           FROM gold.financials_known
                           WHERE company_id = %(c)s AND known_from <= %(d)s
                             AND (known_to IS NULL OR known_to > %(d)s)),
                p AS (SELECT {KEY}, gold.input_values(inputs) AS iv
                      FROM gold.financial_rows(ARRAY[%(c)s], %(d)s))
                SELECT (SELECT count(*) FROM (SELECT * FROM h EXCEPT ALL SELECT * FROM p) x),
                       (SELECT count(*) FROM (SELECT * FROM p EXCEPT ALL SELECT * FROM h) x),
                       (SELECT count(*) FROM h)
                """, {"d": d, "c": company}).fetchone()
            assert diff[:2] == (0, 0), (ticker, d, diff)
            checked += diff[2]
        # today's open versions are gold itself
        today = gold.execute(
            f"""
            WITH h AS (SELECT {KEY} FROM gold.financials_known
                       WHERE company_id = %(c)s AND known_to IS NULL),
            g AS (SELECT 'quarter' AS span, metric, period_end, period_start, value, unit,
                         value_source, source_tag FROM gold.quarterly_financials
                  WHERE company_id = %(c)s
                  UNION ALL
                  SELECT span, metric, period_end, period_start, value, unit, value_source,
                         source_tag FROM gold.derived_financials WHERE company_id = %(c)s)
            SELECT (SELECT count(*) FROM (SELECT * FROM h EXCEPT ALL SELECT * FROM g) x)
                 + (SELECT count(*) FROM (SELECT * FROM g EXCEPT ALL SELECT * FROM h) x)
            """, {"c": company}).fetchone()[0]
        assert today == 0, ticker
    assert checked > 10_000


def figure(conn, d, company, metric, end, span="quarter"):
    """A figure as believed at the end of day d: the version open then."""
    return conn.execute(
        "SELECT value, accession_no, filed, known_from FROM gold.financials_known"
        " WHERE company_id = %s AND span = %s AND metric = %s AND period_end = %s"
        " AND known_from <= %s AND (known_to IS NULL OR known_to > %s)",
        (company, span, metric, end, d, d)).fetchone()


def test_original_then_restated_each_with_its_filing(gold):
    crwd, end = IDS["CRWD"], date(2025, 1, 31)
    assert figure(gold, date(2025, 3, 9), crwd, "net_income", end) is None  # not filed yet
    first = figure(gold, date(2025, 3, 10), crwd, "net_income", end)
    assert first[0] == Decimal(-92282000) and first[2] == date(2025, 3, 10)
    assert figure(gold, date(2026, 3, 4), crwd, "net_income", end) == first
    later = figure(gold, date(2026, 3, 5), crwd, "net_income", end)
    assert later[0] == Decimal(-88252000) and later[2] == date(2026, 3, 5)
    assert later[3] == date(2026, 3, 5) and later[1] != first[1]
    # today's gold is the restated figure
    assert gold.execute("SELECT value FROM gold.quarterly_financials WHERE company_id = %s"
                        " AND metric = 'net_income' AND period_end = %s",
                        (crwd, end)).fetchone()[0] == Decimal(-88252000)


def test_a_new_filing_changes_only_the_history_from_its_date(gold):
    crwd = IDS["CRWD"]
    with gold.transaction(force_rollback=True):
        before = gold.execute("SELECT * FROM gold.financials_known WHERE company_id = %s"
                              " ORDER BY span, metric, period_end, known_from",
                              (crwd,)).fetchall()
        assert gold.execute("SELECT gold.refresh_known(%s)", ([crwd],)).fetchone()[0] == 0
        # a 10-Q filed after every fixture filing restates one quarter's revenue
        cik = gold.execute("SELECT cik FROM silver.company_ciks WHERE company_id = %s",
                           (crwd,)).fetchone()[0]
        gold.execute(
            """
            INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,
                                                value, accession_no, form, filed, taxonomy)
            SELECT cik, metric, tag, unit, period_start, period_end, value + 1000000,
                   '0001535527-26-999999', '10-Q', DATE '2026-12-01', taxonomy
            FROM silver.financial_facts
            WHERE cik = %s AND metric = 'revenue' AND period_end = DATE '2025-10-31'
              AND period_type = 'quarter'
            ORDER BY filed DESC LIMIT 1
            """, (cik,))
        written = gold.execute("SELECT gold.refresh_financials(%s)", ([crwd],)).fetchone()[0]
        assert written > 0
        after = gold.execute("SELECT * FROM gold.financials_known WHERE company_id = %s"
                             " ORDER BY span, metric, period_end, known_from",
                             (crwd,)).fetchall()
        # rows are (company_id, span, metric, period_end, known_from, known_to, ...)
        new = [r for r in after if r not in before]
        gone = [r for r in before if r not in after]
        assert gone and all(r[5] is None for r in gone)  # only versions believed until then
        assert all(r[4] == date(2026, 12, 1) or r[5] == date(2026, 12, 1) for r in new)
        # as of the day before, nothing moved
        assert figure(gold, date(2026, 11, 30), crwd, "revenue", date(2025, 10, 31))[0] \
            != figure(gold, date(2026, 12, 1), crwd, "revenue", date(2025, 10, 31))[0]


# --- the reads as of a day (087, query_api.point_in_time) ----------------------------------

def test_gold_as_of_a_day_is_the_history_believed_then(gold):
    for ticker, company in sorted(IDS.items()):
        for d in (date(2024, 6, 30), date(2025, 3, 10), date(2026, 3, 5), date(2026, 11, 16)):
            got = gold.execute(
                f"SELECT {KEY}, accession_no, known_from FROM gold.financials_at(%s)"
                f" WHERE company_id = %s AND (span = 'quarter' OR value_source <> 'reported')"
                f" ORDER BY {KEY}", (d, company)).fetchall()
            want = gold.execute(
                f"SELECT {KEY}, accession_no, known_from FROM gold.financials_known"
                f" WHERE company_id = %s AND known_from <= %s"
                f" AND (known_to IS NULL OR known_to > %s) ORDER BY {KEY}",
                (company, d, d)).fetchall()
            assert got == want, (ticker, d)
    crwd = IDS["CRWD"]
    assert figure(gold, date(2026, 3, 4), crwd, "net_income", date(2025, 1, 31))[0] == \
        gold.execute("SELECT value FROM gold.financials_at('2026-03-04') WHERE company_id = %s"
                     " AND span = 'quarter' AND metric = 'net_income'"
                     " AND period_end = '2025-01-31'", (crwd,)).fetchone()[0]


def kinds_of(conn, company):
    from query_api import point_in_time

    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT * FROM gold.restatements(%s)", (company,))
        rows = cur.fetchall()
    return rows, point_in_time.restatement_rows(company, rows)


def test_the_three_kinds_of_restatement(gold):
    _, bkng = kinds_of(gold, IDS["BKNG"])
    eps = [r for r in bkng if r["metric"] == "eps_diluted" and r["span"] == "quarter"
           and r["period_end"] == date(2025, 3, 31)]
    assert [(r["original"]["value"], r["restated"]["value"], r["kind"]) for r in eps] == [
        (Decimal("10.07"), Decimal("0.40"), "split")]
    assert {r["kind"] for r in bkng if r["metric"] == "weighted_shares_diluted"} == {"split"}

    _, plxs = kinds_of(gold, IDS["PLXS"])
    moved = [r for r in plxs if r["metric"] == "revenue" and r["span"] == "quarter"
             and r["period_end"] == date(2023, 12, 30)]
    assert [(r["original"]["xbrl_tag"], r["restated"]["xbrl_tag"], r["kind"]) for r in moved] == [
        ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
         "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "another_line")]

    _, crwd = kinds_of(gold, IDS["CRWD"])
    same = [r for r in crwd if r["metric"] == "net_income" and r["span"] == "quarter"
            and r["period_end"] == date(2025, 1, 31)]
    assert [(r["kind"], r["original"]["filed"], r["restated"]["known_from"]) for r in same] == [
        ("same_line", date(2025, 3, 10), date(2026, 3, 5))]
    # as of a day before the restating filing there is none
    assert not [r for r in gold.execute("SELECT metric, period_end FROM gold.restatements(%s, %s)",
                                        (IDS["CRWD"], date(2026, 3, 4))).fetchall()
                if r == ("net_income", date(2025, 1, 31))]


def company_rows(conn, user, company, d):
    with conn.transaction():
        conn.execute("SET LOCAL ROLE query_api")
        conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        rows = conn.execute(
            f"SELECT {KEY} FROM gold.company_financials_at(%s, %s) ORDER BY {KEY}",
            (company, d)).fetchall()
        conn.execute("RESET ROLE")  # a savepoint's SET LOCAL outlives it
        return rows


def test_the_single_company_read_with_and_without_history(gold):
    crwd, d = IDS["CRWD"], date(2025, 9, 1)
    built = company_rows(gold, USER, crwd, d)
    assert built
    want = gold.execute(
        f"""
        SELECT {KEY} FROM gold.financials_at(%s) WHERE company_id = %s ORDER BY {KEY}
        """, (d, crwd)).fetchall()
    assert built == want
    # nobody else reads it, whatever the path
    assert company_rows(gold, "someone-else", crwd, d) == []
    # history missing (a newer rules version): replayed at read time, the same figures
    with gold.transaction(force_rollback=True):
        gold.execute("UPDATE ops.company_builds SET version = 0 WHERE kind = 'known'"
                     " AND company_id = %s", (crwd,))
        assert company_rows(gold, USER, crwd, d) == built
        assert company_rows(gold, "someone-else", crwd, d) == []


# --- the Query API's reads as of a day (query_api.point_in_time) -------------------------------

SECRET = "point-in-time-test-secret-of-32-bytes"


class OneConnection:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn


class Open:
    """The catalog open at financials: no id list reaches the query; RLS bounds the values."""

    def can(self, *_):
        return True

    def access(self, _user):
        from query_api.authorizer import Access
        return Access(frozenset({"financials"}), {})


def call(conn, path, **params):
    from fastapi.testclient import TestClient

    from query_api.app import create_app
    from query_api.auth import mint
    from query_api.store import Store

    client = TestClient(create_app(Open(), Store(OneConnection(conn)), SECRET,
                                   today=lambda: date(2026, 11, 16)))
    return client.get(path, params=params,
                      headers={"Authorization": f"Bearer {mint(USER, SECRET)}"})


def get(conn, path, **params):
    r = call(conn, path, **params)
    assert r.status_code == 200, r.text
    return r.json()


def test_financials_as_of_a_day_and_the_original_beside_the_restated(gold):
    crwd = IDS["CRWD"]
    path = f"/v1/companies/{crwd}/financials"
    for day, value, filed in (("2025-03-10", -92282000, "2025-03-10"),
                              ("2026-03-05", -88252000, "2026-03-05")):
        body = get(gold, path, metrics="net_income", quarters=8, as_of=day)
        assert body["known_on"] == day and "as_of" in body
        row = next(r for r in body["rows"] if r["period_end"] == "2025-01-31")
        assert Decimal(str(row["value"])) == value and row["citation"]["filed"] == filed
        assert "original" not in row  # opt-in only: the JSON is as before without it
    body = get(gold, path, metrics="net_income", quarters=8, originals="true")
    row = next(r for r in body["rows"] if r["period_end"] == "2025-01-31")
    assert row["original"]["kind"] == "same_line"
    assert Decimal(str(row["original"]["value"])) == -92282000
    assert row["original"]["filed"] == "2025-03-10"
    assert next(r for r in body["rows"] if r["period_end"] == "2025-10-31")["original"] is None
    assert call(gold, path, as_of="2026-11-17").status_code == 422  # after today
    assert call(gold, path, as_of="2008-12-31").status_code == 422  # before XBRL


def test_the_screener_as_of_a_day_reads_what_the_company_page_reads(gold):
    columns = ["revenue_ttm", "net_income_q", "total_assets_q", "gross_margin_ttm",
               "eps_diluted_q"]
    for day in ("2025-06-30", "2026-03-05", "2026-11-16"):
        body = get(gold, "/v1/screener", columns=columns, company_id=sorted(IDS.values()),
                   as_of=day, limit=50)
        assert body["known_on"] == day and len(body["rows"]) == len(IDS)
        for row in body["rows"]:
            for col in columns:
                metric, view = col.rsplit("_", 1)
                page = get(gold, f"/v1/companies/{row['company_id']}/financials",
                           metrics=metric, quarters=1, view="ttm" if view == "ttm" else "quarter",
                           as_of=day)["rows"]
                want = page[0] if page else None
                got = row["values"][col]
                assert (None if want is None else Decimal(str(want["value"]))) == \
                    (None if got is None else Decimal(str(got))), (row["company_id"], col, day)
                if want is not None:
                    assert row["sources"][col]["accession_no"] == \
                        want["citation"]["accession_no"]


def test_a_screen_today_after_one_as_of_a_day_on_the_same_connection(gold):
    """The as-of session sets tributary.today locally: on a pooled connection it reads '' after,
    which every reader must take as today (087's gold.company_confidence, gold.is_stale)."""
    ids = sorted(IDS.values())
    old = get(gold, "/v1/screener", company_id=ids, as_of="2025-06-30", limit=50)
    assert all(r["confidence"] is None for r in old["rows"])  # today's only
    # here the fixture's one transaction keeps the setting; a pooled connection reads '' next
    gold.execute("SELECT set_config('tributary.today', '', true)")
    now = get(gold, "/v1/screener", company_id=ids, limit=50)
    assert "known_on" not in now
    assert any(r["confidence"] for r in now["rows"])


def test_per_share_applies_only_the_splits_restated_by_the_day(gold):
    bkng = IDS["BKNG"]
    before = get(gold, f"/v1/companies/{bkng}/per-share", as_of="2026-01-15")
    after = get(gold, f"/v1/companies/{bkng}/per-share", as_of="2026-11-16")
    assert before["splits"] == [] and after["splits"]
    assert after["splits"][0]["label"] == "25-for-1"
    q1 = [r for r in before["rows"] if r["metric"] == "eps_diluted"
          and r["period_end"] == "2025-03-31"]
    assert q1 and Decimal(str(q1[0]["value"])) == Decimal("10.07")


def test_the_restatements_route(gold):
    crwd = IDS["CRWD"]
    body = get(gold, f"/v1/companies/{crwd}/restatements")
    assert body["history"] is True
    kinds = {(r["metric"], r["period_end"], r["kind"]) for r in body["rows"]}
    assert ("net_income", "2025-01-31", "same_line") in kinds
    assert ("weighted_shares_diluted", "2025-07-31", "split") in kinds
    early = get(gold, f"/v1/companies/{crwd}/restatements", as_of="2026-03-04")
    assert ("net_income", "2025-01-31") not in {(r["metric"], r["period_end"])
                                              for r in early["rows"]}
