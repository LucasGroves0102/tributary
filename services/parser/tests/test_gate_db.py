"""The gate (migration 167) against a real Postgres: every silver fact of a filing joined to where
its printed inline XBRL puts it (parser v9's silver.printed_facts) and its printed value checked;
the catch-up's backlog; the printed_mismatch finding. Skipped unless TEST_DATABASE_URL is set; every
test runs inside a transaction that is rolled back. The filing is NVIDIA's 10-Q (fixtures)."""

import gzip
import os
from decimal import Decimal
from pathlib import Path

import pytest

from filing_parser import catchup
from filing_parser.db import write_sections
from filing_parser.sections import parse_document

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

CIK = "0001045810"
ACC = "0001045810-26-000075"
TEN_Q = gzip.decompress((Path(__file__).parent / "fixtures" / "nvda-10-Q.htm.gz").read_bytes())
Q, Q_YEAR_AGO = ("2026-04-27", "2026-07-26"), ("2025-04-28", "2025-07-27")
# (metric, tag, unit, start, end, value): what silver holds for the filing
FACTS = [
    ("revenue", "Revenues", "USD", *Q, "96221000000"),               # agrees, in Item 1
    ("revenue", "Revenues", "USD", *Q_YEAR_AGO, "46743000000"),      # agrees: the comparative
    ("net_income", "NetIncomeLoss", "USD", *Q, "59689000000"),       # the filing prints 59,688
    ("eps_diluted", "EarningsPerShareDiluted", "USD/shares", *Q, "2.46"),
    ("total_assets", "Assets", "USD", None, "2026-07-26", "320272000000"),
    ("revenue", "Revenues", "USD", "2024-04-29", "2024-07-28", "30040000000"),  # not printed
    ("total_assets", "Assets[a:X+a:Y]", "USD", None, "2026-07-26", "1"),         # a member sum
]


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE silver.filing_sections, bronze.filing_documents,"
                  " silver.financial_facts, ops.quality_findings, ops.catchup_runs,"
                  " silver.company_aliases,"
                  " silver.company_ciks, silver.companies CASCADE")
        c.execute("UPDATE ops.catchup SET paused = false, version = NULL, started_at = NULL")
        c.execute("INSERT INTO silver.companies (company_id, name, current_cik)"
                  " VALUES (%s, 'NVIDIA CORP', %s)", (CIK, CIK))
        c.execute("INSERT INTO silver.company_ciks VALUES (%s, %s)", (CIK, CIK))
        c.execute(
            "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
            " url, content_gzip, content_sha256, size_bytes) VALUES (%s, %s, '10-Q',"
            " '2026-08-27', 'nvda-20260726.htm', 'https://example/q', %s, 'x', %s)",
            (ACC, CIK, gzip.compress(TEN_Q), len(TEN_Q)))
        for metric, tag, unit, start, end, value in FACTS:
            c.execute(
                "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start,"
                " period_end, value, accession_no, form, filed, taxonomy, source) VALUES"
                " (%s, %s, %s, %s, %s, %s, %s, %s, '10-Q', '2026-08-27', 'us-gaap',"
                " 'companyfacts')", (CIK, metric, tag, unit, start, end, value, ACC))
        yield c


def printed(conn) -> None:
    sections, facts = parse_document(TEN_Q, "10-Q")
    write_sections(conn, ACC, sections, facts)


def lines(conn) -> dict[tuple, dict]:
    (rows,) = conn.execute("SELECT lines FROM silver.fact_gates WHERE accession_no = %s",
                           (ACC,)).fetchone()
    return {(r["t"], r.get("s"), r["e"]): r for r in rows}


def test_printed_value_reads_the_formats():
    with psycopg.connect(DSN) as c:
        def value(text, fmt=None, scale=None, sign=None):
            return c.execute("SELECT silver.printed_value(%s, %s, %s, %s)",
                             (text, fmt, scale, sign)).fetchone()[0]
        assert value("96,221", "ixt:num-dot-decimal", "6") == 96221000000
        assert value("1,2 34", "ixt:numdotdecimal", "", "-") == -1234
        assert value("1.234,5", "ixt:num-comma-decimal", "3") == Decimal("1234500")
        assert value(" 3.91 ") == Decimal("3.91") and value(".00", None, None, "-") == 0
        assert value("—", "ixt:fixed-zero") == 0 and value("-", "ixt:num-dot-decimal") == 0
        assert value("—") is None and value("1,234") is None  # no format: a plain decimal
        assert value("no", "ixt-sec:numwordsen") == 0 and value("Zero.", "ixt-sec:numwordsen") == 0
        assert value("twenty-five", "ixt-sec:numwordsen") == 25
        assert value("seven", "ixt-sec:numwordsen", "3") == 7000
        assert value("a few", "ixt-sec:numwordsen") is None
        assert value("1", "ixt:date-monthname-day-year-en") is None
        assert value("5", None, "x") is None
        assert c.execute("SELECT silver.printed_round(96221000000, '-8'),"
                         " silver.printed_round(2.456, '2'), silver.printed_round(7, 'INF'),"
                         " silver.printed_round(7, NULL)").fetchone() == (
            Decimal("96200000000"), Decimal("2.46"), 7, 7)


def test_every_fact_of_the_filing_gets_a_status_and_its_line(conn):
    printed(conn)
    assert conn.execute("SELECT silver.gate_facts(%s)", ([ACC],)).fetchone() == (len(FACTS),)
    gate = conn.execute("SELECT facts, agrees, disagrees, not_located, not_applicable, parser,"
                        " gate_version, cik FROM silver.fact_gates").fetchall()
    assert gate == [(7, 4, 1, 1, 1, "python", 167, CIK)]
    found = lines(conn)
    revenue = found[("Revenues", *Q)]
    assert (revenue["st"], revenue["k"], revenue["c"], revenue["u"]) == ("a", "I-1", CIK, "USD")
    text, start = conn.execute("SELECT text, char_start FROM silver.filing_sections"
                               " WHERE accession_no = %s AND section_key = 'I-1'",
                               (ACC,)).fetchone()
    assert text[revenue["a"] - start:revenue["b"] - start] == "96,221"
    assert found[("Revenues", *Q_YEAR_AGO)]["st"] == "a"
    assert found[("EarningsPerShareDiluted", *Q)]["st"] == "a"
    assets = found[("Assets", None, "2026-07-26")]
    assert assets["st"] == "a" and text[assets["a"] - start:assets["b"] - start] == "320,272"
    assert (found[("Revenues", "2024-04-29", "2024-07-28")]["st"],
            found[("Revenues", "2024-04-29", "2024-07-28")]["r"]) == ("n", "not_printed")
    assert "k" not in found[("Revenues", "2024-04-29", "2024-07-28")]
    assert (found[("Assets[a:X+a:Y]", None, "2026-07-26")]["st"],
            found[("Assets[a:X+a:Y]", None, "2026-07-26")]["r"]) == ("x", "member")
    # the disagreement: both values and where the filing prints the other one
    net = found[("NetIncomeLoss", *Q)]
    assert net["st"] == "d"
    (row,) = conn.execute("SELECT metric, value, printed, section_key, char_start, char_end"
                          " FROM silver.fact_disagreements").fetchall()
    assert row[:4] == ("net_income", Decimal("59689000000"), Decimal("59688000000"), "I-1")
    assert text[row[4] - start:row[5] - start] == "59,688" and (row[4], row[5]) == (
        net["a"], net["b"])


def test_a_filing_without_printed_facts_has_no_gate_and_a_regate_replaces_it(conn):
    assert conn.execute("SELECT silver.gate_facts(%s)", ([ACC],)).fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM silver.fact_gates").fetchone() == (0,)
    printed(conn)
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    conn.execute("UPDATE silver.financial_facts SET value = 59688000000"
                 " WHERE tag = 'NetIncomeLoss'")
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    assert conn.execute("SELECT agrees, disagrees FROM silver.fact_gates").fetchall() == [(5, 0)]
    assert conn.execute("SELECT count(*) FROM silver.fact_disagreements").fetchone() == (0,)


def test_the_backlog_and_the_catchup_kind(conn):
    def backlog():
        return [r[0] for r in conn.execute("SELECT accession_no FROM ops.gate_backlog(167)")]
    assert backlog() == []
    printed(conn)
    assert backlog() == [ACC]
    r = catchup.gates(conn, 60)
    assert (r.kind, r.version, r.done, r.behind) == ("gate", "167", 1, 0)
    assert backlog() == []
    # printed facts rewritten, the filer's gold rebuilt, another rule version: due again
    conn.execute("UPDATE silver.printed_facts SET written_at = clock_timestamp()")
    assert backlog() == [ACC]
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    conn.execute("INSERT INTO ops.company_builds (kind, company_id, version, built_at)"
                 " VALUES ('gold', %s, 165, clock_timestamp())"
                 " ON CONFLICT (kind, company_id) DO UPDATE SET built_at = clock_timestamp()",
                 (CIK,))
    assert backlog() == [ACC]
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    assert backlog() == []
    assert [r[0] for r in conn.execute("SELECT accession_no FROM ops.gate_backlog(168)")] == [ACC]
    # the company's financial check is due once a filing of it is gated since its check
    assert CIK in [r[0] for r in conn.execute(
        "SELECT company_id FROM ops.company_backlog('check_financials')")]


def test_a_disagreement_is_a_printed_mismatch_finding_without_values(conn):
    printed(conn)
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    conn.execute("SELECT ops.check_financials(%s)", ([CIK],))
    rows = conn.execute("SELECT metric, period_end::text, severity, detail FROM"
                        " ops.quality_findings WHERE check_name = 'printed_mismatch'").fetchall()
    assert len(rows) == 1
    metric, end, severity, detail = rows[0]
    assert (metric, end, severity) == ("net_income", "2026-07-26", "warning")
    assert detail["tag"] == "NetIncomeLoss" and detail["accession_no"] == ACC
    assert detail["section_key"] == "I-1" and detail["facts"] == 1
    assert abs(detail["factor"] - 59689 / 59688) < 1e-6
    assert not {"value", "printed", "gold"} & set(detail)
    tiers = conn.execute("SELECT findings, tier FROM ops.quality_tiers"
                         " WHERE check_name = 'printed_mismatch'").fetchall()
    assert tiers == [(1, "warning")]


def test_gold_figures_inherit_their_lines_and_trust_is_counted(conn):
    """168: a reported gold figure's line is its source fact's (gold.printed_lines, read time);
    ops.refresh_trust counts the figures linked, agreeing and on a disagreeing fact."""
    printed(conn)
    conn.execute("SELECT silver.gate_facts(%s)", ([ACC],))
    conn.execute("DELETE FROM gold.quarterly_financials WHERE company_id = %s", (CIK,))
    for metric, tag, value, source in (
            ("revenue", "us-gaap:Revenues", 96221000000, "reported"),
            ("net_income", "us-gaap:NetIncomeLoss", 59689000000, "reported"),
            ("eps_diluted", "us-gaap:EarningsPerShareDiluted", 2.46, "derived")):
        conn.execute(
            "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
            " value, unit, value_source, source_tag, accession_no, form, filed,"
            " source_accessions, ingested_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " '10-Q', '2026-08-27', ARRAY[%s], now())",
            (CIK, metric, Q[0], Q[1], value, "USD/shares" if metric == "eps_diluted" else "USD",
             source, tag, ACC, ACC))
    lines = conn.execute(
        "SELECT n, status, section_key, char_start, char_end FROM gold.printed_lines(%s, %s, %s,"
        " %s::date[], %s::date[]) ORDER BY n",
        ([ACC, ACC, ACC], ["Revenues", "NetIncomeLoss", "Revenues"], ["USD", "USD", "USD"],
         [Q[0], Q[0], "2024-04-29"], [Q[1], Q[1], "2024-07-28"])).fetchall()
    assert [(n, status, key) for n, status, key, _, _ in lines] == [
        (1, "a", "I-1"), (2, "d", "I-1"), (3, "n", None)]
    text, start = conn.execute("SELECT text, char_start FROM silver.filing_sections"
                               " WHERE accession_no = %s AND section_key = 'I-1'",
                               (ACC,)).fetchone()
    assert text[lines[0][3] - start:lines[0][4] - start] == "96,221"
    at = conn.execute("SELECT ops.refresh_trust()").fetchone()[0]
    run = conn.execute("SELECT figures, since_ixbrl, reported, linked, agreeing, disagreeing,"
                       " filings_gated, facts_gated, facts_disagree FROM ops.trust_runs"
                       " WHERE run_at = %s", (at,)).fetchone()
    assert run == (3, 3, 2, 2, 1, 1, 1, 7, 1)
    assert conn.execute("SELECT linked, agreeing FROM ops.public_trust()").fetchone() == (2, 1)
