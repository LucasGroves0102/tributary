"""Stored documents → bronze.filing_xbrl → silver.segment_facts → gold.segment_financials and the
segment quality check, against a real Postgres with migrations applied. Real documents (NVIDIA,
PepsiCo, Apple, Coca-Cola fixtures) plus small synthetic iXBRL documents for renames and TTM.
Skipped unless TEST_DATABASE_URL is set; every test is rolled back."""

import gzip
import hashlib
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from facts_fetcher import segments
from facts_fetcher.ixbrl import EXTRACTOR_VERSION

psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")

HERE = Path(__file__).parent
PARSER = HERE.parents[1] / "parser" / "tests" / "fixtures"
IXBRL = HERE / "fixtures" / "ixbrl"
NVDA, PEP, AAPL, KO = "0001045810", "0000077476", "0000320193", "0000021344"
X, Y = "9000000003", "9000000004"  # synthetic companies
COMPANIES = {NVDA: "0126", PEP: "1227", AAPL: "0927", KO: "1231", X: "1231", Y: "1231"}
DOCS = [  # accession, cik, form, filed, file
    ("0001045810-26-000021", NVDA, "10-K", date(2026, 2, 25), PARSER / "nvda-10-K.htm.gz"),
    ("0001045810-26-000075", NVDA, "10-Q", date(2026, 8, 26), PARSER / "nvda-10-Q.htm.gz"),
    ("0000077476-25-000053", PEP, "10-Q", date(2025, 10, 9), IXBRL / "pep-10-Q-2025Q3.htm.gz"),
    ("0000077476-26-000007", PEP, "10-K", date(2026, 2, 3), IXBRL / "pep-10-K-2025.htm.gz"),
    ("0000320193-25-000079", AAPL, "10-K", date(2025, 10, 31), IXBRL / "aapl-10-K-2025.htm.gz"),
    ("0001628280-26-010047", KO, "10-K", date(2026, 2, 20), IXBRL / "ko-10-K-2025.htm.gz"),
]
PRODUCT, SEGMENT = "srt:ProductOrServiceAxis", "us-gaap:StatementBusinessSegmentsAxis"


def mini_doc(cik: str, facts: list[tuple[str, str | None, str, str, str]]) -> bytes:
    """A small iXBRL document: (concept, product member or None, start, end, value) facts."""
    contexts, body = [], []
    for i, (concept, member, start, end, value) in enumerate(facts):
        seg = (f'<xbrli:segment><xbrldi:explicitMember dimension="{PRODUCT}">{member}'
               "</xbrldi:explicitMember></xbrli:segment>") if member else ""
        contexts.append(
            f'<xbrli:context id="c{i}"><xbrli:entity><xbrli:identifier'
            f' scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>{seg}</xbrli:entity>'
            f"<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}"
            "</xbrli:endDate></xbrli:period></xbrli:context>")
        body.append(f'<p><ix:nonFraction name="{concept}" contextRef="c{i}" unitRef="usd"'
                    f' decimals="0" format="ixt:num-dot-decimal">{value}</ix:nonFraction></p>')
    html = ("<html><body><div style='display:none'><ix:header><ix:resources>"
            + "".join(contexts)
            + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
            "</ix:resources></ix:header></div>" + "".join(body) + "</body></html>")
    return gzip.compress(html.encode())


def add_document(conn, accession, cik, form, filed, content: bytes) -> None:
    sha = hashlib.sha256(gzip.decompress(content)).hexdigest()
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name,"
        " url, content_gzip, content_sha256, size_bytes) VALUES (%s, %s, %s, %s, 'd.htm', 'u',"
        " %s, %s, %s)", (accession, cik, form, filed, content, sha, len(content)))


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c, c.transaction(force_rollback=True):
        c.execute("TRUNCATE gold.segment_financials, silver.segment_facts, bronze.filing_xbrl,"
                  " gold.quarterly_financials, gold.derived_financials, ops.quality_findings,"
                  " ops.quality_companies, bronze.filing_documents CASCADE")
        for cid, fye in COMPANIES.items():
            c.execute("DELETE FROM silver.company_ciks WHERE cik = %s", (cid,))
            c.execute("INSERT INTO silver.companies (company_id, name, current_cik,"
                      " fiscal_year_end) VALUES (%s, %s, %s, %s) ON CONFLICT (company_id)"
                      " DO UPDATE SET fiscal_year_end = EXCLUDED.fiscal_year_end",
                      (cid, cid, cid, fye))
            c.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                      (cid, cid))
        for accession, cik, form, filed, path in DOCS:
            add_document(c, accession, cik, form, filed, path.read_bytes())
        yield c


def gold(conn, company, member, metric="revenue", span="quarter"):
    return conn.execute(
        "SELECT period_start, period_end, value, value_source, source_accessions, member_label,"
        "       fiscal_year, fiscal_quarter, calendar_year, calendar_quarter, member_aliases"
        " FROM gold.segments WHERE company_id = %s AND member = %s AND metric = %s"
        " AND span = %s ORDER BY period_end", (company, member, metric, span)).fetchall()


def test_load_extracts_every_document_and_is_resumable(conn):
    first = segments.run(conn, limit=4, batch_size=3)
    assert (first.documents, first.completed, first.batches) == (4, False, [3, 1])
    second = segments.run(conn, limit=10, batch_size=3)
    assert (second.documents, second.completed) == (2, True)
    assert segments.run(conn).documents == 0  # checkpoint: nothing left
    rows = conn.execute("SELECT status, extractor_version, document_sha256 = d.content_sha256"
                        " FROM bronze.filing_xbrl x JOIN bronze.filing_documents d"
                        " USING (accession_no)").fetchall()
    assert rows == [("ok", EXTRACTOR_VERSION, True)] * len(DOCS)
    status = conn.execute("SELECT sum(documents), sum(pending), sum(ok)"
                          " FROM ops.segment_load_status").fetchone()
    assert status == (len(DOCS), 0, len(DOCS))


def test_changed_document_is_extracted_again(conn):
    segments.run(conn)
    conn.execute("UPDATE bronze.filing_documents SET content_sha256 = 'changed'"
                 " WHERE accession_no = %s", (DOCS[0][0],))
    assert segments.run(conn).documents == 1


def test_silver_classifies_segment_facts(conn):
    segments.run(conn)
    rows = dict(conn.execute(
        "SELECT axis, count(*) FROM silver.segment_facts WHERE cik = %s GROUP BY axis",
        (NVDA,)).fetchall())
    assert set(rows) == {"business_segment", "geography", "product_service", "reconciliation",
                         "total"}
    # segment figures tagged with ConsolidationItemsAxis = OperatingSegmentsMember count once
    gaming = conn.execute(
        "SELECT axis, member_label, value FROM silver.segment_facts WHERE cik = %s"
        " AND member = 'nvda:GraphicsSegmentMember' AND metric = 'operating_income'"
        " AND period_end = '2026-01-25'", (NVDA,)).fetchall()
    assert gaming == [("business_segment", "Graphics", Decimal("9156000000"))]
    assert conn.execute("SELECT count(*) FROM silver.segment_facts"
                        " WHERE member = 'us-gaap:OperatingSegmentsMember'").fetchone()[0] == 0


def test_q4_derived_from_annual_minus_nine_months_with_a_16_week_quarter(conn):
    segments.run(conn)
    rows = {r[1]: r for r in gold(conn, PEP, "pep:PepsiCoBeveragesNorthAmericaMember")}
    assert rows[date(2025, 9, 6)][:4] == (date(2025, 6, 15), date(2025, 9, 6),
                                          Decimal("7327000000"), "reported")
    q4 = rows[date(2025, 12, 27)]
    assert q4[:4] == (date(2025, 9, 7), date(2025, 12, 27),
                      Decimal("28197000000") - Decimal("19999000000"), "derived")
    assert (q4[1] - q4[0]).days + 1 == 112  # 16 weeks
    assert q4[4] == ["0000077476-25-000053", "0000077476-26-000007"]
    assert q4[5] == "PepsiCo Beverages North America"
    assert q4[6:10] == (2025, 4, 2025, 4)  # fiscal and calendar Q4 2025


def test_nvidia_quarter_labels_and_calendar(conn):
    segments.run(conn)
    rows = gold(conn, NVDA, "nvda:DataCenterMember")
    q2 = [r for r in rows if r[1] == date(2026, 7, 26)][0]
    assert (q2[2], q2[3], q2[5]) == (Decimal("89023000000"), "reported", "Data Center")
    assert q2[6:10] == (2027, 2, 2026, 2)  # fiscal Q2 FY2027 = calendar Q2 2026


def test_annual_only_filings_give_no_quarters(conn):
    segments.run(conn)
    assert gold(conn, AAPL, "aapl:IPhoneMember") == []
    labels = conn.execute("SELECT DISTINCT member_label FROM silver.segment_facts"
                          " WHERE cik = %s AND axis = 'product_service'", (AAPL,)).fetchall()
    assert {"iPhone", "iPad", "Mac", "Wearables, Home and Accessories", "Products",
            "Services"} == {r[0] for r in labels}


def test_renamed_members_are_merged(conn):
    tag = "us-gaap:Revenues"
    q1_24, q1_25 = ("2024-01-01", "2024-03-31"), ("2025-01-01", "2025-03-31")
    add_document(conn, "9000000003-24-000001", X, "10-Q", date(2024, 5, 1), mini_doc(X, [
        (tag, "x:OldWidgetsMember", *q1_24, "100"),
        (tag, "x:GadgetsSegmentMember", *q1_24, "50"),
        (tag, "x:SpareMember", *q1_24, "5"),
    ]))
    add_document(conn, "9000000003-25-000001", X, "10-Q", date(2025, 5, 1), mini_doc(X, [
        (tag, "x:WidgetsAndGizmosMember", *q1_25, "120"),
        (tag, "x:WidgetsAndGizmosMember", *q1_24, "100"),  # restated comparative
        (tag, "x:GadgetsMember", *q1_25, "60"),
        (tag, "x:OtherNewMember", *q1_25, "7"),
        (tag, "x:OtherNewMember", *q1_24, "6"),
    ]))
    segments.run(conn)
    members = conn.execute(
        "SELECT member, member_label, member_aliases, array_agg(value ORDER BY period_end)"
        " FROM gold.segment_financials WHERE company_id = %s AND span = 'quarter'"
        " GROUP BY 1, 2, 3 ORDER BY 1", (X,)).fetchall()
    assert members == [
        ("x:GadgetsMember", "Gadgets", ["x:GadgetsSegmentMember"],
         [Decimal(50), Decimal(60)]),                          # same label key
        ("x:OtherNewMember", "Other New", [], [Decimal(6), Decimal(7)]),
        ("x:SpareMember", "Spare", [], [Decimal(5)]),          # no equal value: not merged
        ("x:WidgetsAndGizmosMember", "Widgets and Gizmos", ["x:OldWidgetsMember"],
         [Decimal(100), Decimal(120)]),                        # equal restated value
    ]


def test_ttm_sums_four_contiguous_quarters(conn):
    tag = "us-gaap:Revenues"
    quarters = [("2025-01-01", "2025-03-31"), ("2025-04-01", "2025-06-30"),
                ("2025-07-01", "2025-09-30"), ("2025-10-01", "2025-12-31")]
    add_document(conn, "9000000004-26-000001", Y, "10-K", date(2026, 2, 1), mini_doc(Y, [
        (tag, "y:AlphaMember", s, e, str(i + 1)) for i, (s, e) in enumerate(quarters)]))
    segments.run(conn)
    ttm = gold(conn, Y, "y:AlphaMember", span="ttm")
    assert [(r[0], r[1], r[2]) for r in ttm] == [(date(2025, 1, 1), date(2025, 12, 31),
                                                 Decimal(10))]


def add_total(conn, company, metric, start, end, value):
    conn.execute(
        "INSERT INTO gold.quarterly_financials (company_id, metric, period_start, period_end,"
        " value, unit, value_source, source_tag, accession_no, filed, source_accessions,"
        " ingested_at) VALUES (%s, %s, %s, %s, %s, 'USD', 'reported', 'us-gaap:Revenues',"
        " 'acc', '2026-01-01', ARRAY['acc'], now())", (company, metric, start, end, value))


def findings(conn, company):
    return conn.execute("SELECT check_name, metric, period_end, detail->>'members'"
                        " FROM ops.quality_findings WHERE company_id = %s ORDER BY 1, 2, 3",
                        (company,)).fetchall()


def test_quality_check_members_against_consolidated_totals(conn):
    add_total(conn, PEP, "revenue", "2025-06-15", "2025-09-06", 23937000000)
    add_total(conn, PEP, "operating_income", "2025-06-15", "2025-09-06", 3569000000)
    add_total(conn, PEP, "revenue", "2025-09-07", "2025-12-27", 1)  # wrong on purpose
    # NVIDIA's Data Center sits next to its parts: one subtotal overlap passes
    add_total(conn, NVDA, "revenue", "2026-04-27", "2026-07-26", 96221000000)
    segments.run(conn)
    assert findings(conn, PEP) == [
        ("segment_sum_business_segment", "revenue", date(2025, 12, 27), "6")]
    assert findings(conn, NVDA) == []
    # the financials check keeps the segment findings
    conn.execute("SELECT ops.check_financials(%s)", ([PEP],))
    assert len(findings(conn, PEP)) == 1
    summary = dict(conn.execute("SELECT check_name, findings FROM ops.quality_summary"
                                " WHERE check_name LIKE 'segment%'").fetchall())
    assert summary["segment_sum_business_segment"] == 1


def test_rebuild_reprojects_without_documents(conn):
    segments.run(conn)
    before = conn.execute("SELECT count(*) FROM gold.segment_financials").fetchone()[0]
    conn.execute("UPDATE bronze.filing_xbrl SET facts = '[]', labels = '{}'")
    conn.execute("UPDATE bronze.filing_documents SET content_gzip = '\\x00'")  # unreadable
    stats = segments.rebuild(conn)
    assert stats.documents == len(DOCS)
    assert conn.execute("SELECT count(*) FROM gold.segment_financials").fetchone()[0] == before
