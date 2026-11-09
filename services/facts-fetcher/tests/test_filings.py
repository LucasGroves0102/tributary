"""A filing's own XBRL where companyfacts doesn't carry it (filings.py, instance.py, migration 055).

Instances: trimmed from the SEC's copies with trim_instance.py (Clorox's FY2026 10-K, whose primary
document carries only the cover page, and NVIDIA's FY2020 10-K with its capex extension tag). The
database tests build each case the way the filing has it and are rolled back."""

import gzip
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from facts_fetcher import filings, instance
from facts_fetcher.filings import Mapping, instance_name, project
from facts_fetcher.segments import compress

HERE = Path(__file__).parent
INSTANCES = HERE / "fixtures" / "instance"


def read(name: str) -> bytes:
    return gzip.decompress((INSTANCES / name).read_bytes())


# instance documents ----------------------------------------------------------------------------

def test_clorox_instance_has_the_statements_its_primary_document_lacks():
    ex = instance.extract_bytes(read("clx-20260630_htm.xml.gz"))
    assert ex.entity_cik == "0000021076"
    got = {(f.concept, f.start, f.end or f.instant): f.value for f in ex.facts if not f.members}
    assert got[("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "2025-07-01",
                "2026-06-30")] == Decimal("6720000000")
    assert got[("us-gaap:NetIncomeLoss", "2025-07-01", "2026-06-30")] == Decimal("587000000")
    assert got[("us-gaap:EarningsPerShareDiluted", "2025-07-01", "2026-06-30")] == Decimal("4.81")
    assert got[("us-gaap:Assets", None, "2026-06-30")] == Decimal("7794000000")
    assert {f.unit for f in ex.facts} == {"USD", "USD/shares"}


def test_an_old_instance_with_a_default_namespace_and_a_company_prefix():
    ex = instance.extract_bytes(read("nvda-2020x10k_htm.xml.gz"))
    capex = {f.end: f.value for f in ex.facts
             if f.concept == "nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets"}
    assert capex == {"2018-01-28": Decimal("593000000"), "2019-01-27": Decimal("600000000"),
                     "2020-01-26": Decimal("489000000")}


def test_prefixes_are_the_standard_ones_whatever_the_file_binds_and_duplicates_keep_precision():
    doc = b"""<?xml version="1.0"?>
<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance" xmlns:g="http://fasb.org/us-gaap/2024"
        xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <x:context id="c"><x:entity>
    <x:identifier scheme="http://www.sec.gov/CIK">0000000001</x:identifier></x:entity><x:period><x:instant>2026-06-30</x:instant></x:period></x:context>
  <x:context id="d"><x:entity>
    <x:identifier scheme="http://www.sec.gov/CIK">0000000001</x:identifier><x:segment>
    <xbrldi:explicitMember dimension="g:StatementBusinessSegmentsAxis"
      >g:AMember</xbrldi:explicitMember></x:segment></x:entity><x:period><x:instant>2026-06-30</x:instant></x:period></x:context>
  <x:unit id="usd"><x:measure>iso4217:USD</x:measure></x:unit>
  <g:Liabilities contextRef="c" unitRef="usd" decimals="-5">26800000</g:Liabilities>
  <g:Liabilities contextRef="c" unitRef="usd" decimals="-3">26775000</g:Liabilities>
  <g:Assets contextRef="c" unitRef="usd" decimals="-6" xsi:nil="true"/>
  <g:Assets contextRef="d" unitRef="usd" decimals="-6">-5000000</g:Assets>
  <g:DocumentType contextRef="c">10-K</g:DocumentType>
</x:xbrl>"""
    ex = instance.extract_bytes(doc)
    assert [(f.concept, f.value, f.members) for f in ex.facts] == [
        ("us-gaap:Liabilities", Decimal("26775000"), ()),
        ("us-gaap:Assets", Decimal("-5000000"),
         (("g:StatementBusinessSegmentsAxis", "g:AMember"),)),
    ]
    assert ex.conflicts == 1


def test_the_instance_among_a_folders_files():
    inline = {"directory": {"item": [
        {"name": "clx-20260630.htm"}, {"name": "clx-20260630_cal.xml"},
        {"name": "clx-20260630_htm.xml"}, {"name": "FilingSummary.xml"}]}}
    old = {"directory": {"item": [{"name": "nvda-20180128.xml"}, {"name": "nvda-20180128_lab.xml"},
                                  {"name": "nvda-20180128.xsd"}, {"name": "FilingSummary.xml"}]}}
    assert instance_name(inline) == "clx-20260630_htm.xml"
    assert instance_name(old) == "nvda-20180128.xml"
    assert instance_name({"directory": {"item": [{"name": "FilingSummary.xml"}]}}) is None


# projection ------------------------------------------------------------------------------------

M = Mapping(standard=frozenset({("us-gaap", "Revenues"), ("us-gaap", "DebtCurrent")}),
            own={"F": frozenset({("f", "Capex")})},
            sums={"F": {"us-gaap:DebtCurrent": "us-gaap:StatementBusinessSegmentsAxis"}})
SEG = "us-gaap:StatementBusinessSegmentsAxis"


def test_projection_keeps_what_silver_reads():
    facts = [
        {"c": "us-gaap:Revenues", "v": "10", "u": "USD", "s": "2026-01-01", "e": "2026-03-31"},
        {"c": "f:Capex", "v": "2", "u": "USD", "s": "2026-01-01", "e": "2026-03-31"},
        {"c": "us-gaap:Goodwill", "v": "9", "u": "USD", "i": "2026-03-31"},
        {"c": "us-gaap:DebtCurrent", "v": "5", "u": "USD", "i": "2026-03-31", "m": {SEG: "f:A"}},
        {"c": "us-gaap:DebtCurrent", "v": "6", "u": "USD", "i": "2026-03-31",
         "m": {SEG: "f:B", "srt:ConsolidationItemsAxis": "us-gaap:OperatingSegmentsMember"}},
        {"c": "us-gaap:DebtCurrent", "v": "7", "u": "USD", "i": "2026-03-31",
         "m": {SEG: "f:B", "srt:ProductOrServiceAxis": "f:X"}},
        {"c": "us-gaap:Revenues", "v": "4", "u": "USD", "s": "2026-01-01", "e": "2026-03-31",
         "m": {SEG: "f:A"}},
    ]
    gap = [f["v"] for f in project(facts, "F", M, gap=True)]
    assert gap == ["10", "2", "5", "6"]
    # companyfacts has the filing: only what it never carries
    assert [f["v"] for f in project(facts, "F", M, gap=False)] == ["2", "5", "6"]
    assert [f["v"] for f in project(facts, "other", M, gap=True)] == ["10"]


# database ----------------------------------------------------------------------------------------

psycopg = pytest.importorskip("psycopg")
db = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

CO = "0000354950"   # conftest's Home Depot row, re-shaped per test
NVDA = "0001045810"
FILED = date(2026, 5, 7)


def fact(c, v, s=None, e=None, i=None, u="USD", m=None):
    d = {"c": c, "v": str(v), "u": u, "d": "-6"}
    if i:
        d["i"] = i
    else:
        d["s"], d["e"] = s, e
    if m:
        d["m"] = m
    return d


def stored(conn, accession, cik, facts, form="10-Q", filed=FILED, entity=None, filers=None):
    """A stored primary document with its extract, and the filing in the submissions index."""
    conn.execute(
        "INSERT INTO bronze.filing_documents (accession_no, cik, form, filed, document_name, url,"
        " content_gzip, content_sha256, size_bytes)"
        " VALUES (%s, %s, %s, %s, 'q.htm', 'https://www.sec.gov/q.htm', '\\x00', 'x', 1)",
        (accession, cik, form, filed))
    conn.execute(
        "INSERT INTO bronze.filing_xbrl (accession_no, cik, entity_cik, document_sha256,"
        " extractor_version, status, raw_gzip, fact_count)"
        " VALUES (%s, %s, %s, 'x', 1, 'ok', %s, %s)",
        (accession, cik, entity or cik, compress(facts), len(facts)))
    index(conn, accession, filers or [cik], form, filed)


def index(conn, accession, ciks, form="10-Q", filed=FILED):
    for cik in ciks:
        conn.execute(
            "INSERT INTO bronze.submission_filings (accession_no, cik, form, filing_date, is_xbrl)"
            " VALUES (%s, %s, %s, %s, true)", (accession, cik, form, filed))


def statements(n=12, start="2026-01-01", end="2026-03-31", revenue=24633):
    """A 10-Q's undimensioned facts: revenue, net income, and enough balance sheet lines that the
    extract counts as having the statements (MIN_STATEMENT_FACTS)."""
    out = [fact("us-gaap:Revenues", revenue * 10**6, start, end),
           fact("us-gaap:NetIncomeLoss", 5785 * 10**6, start, end),
           fact("us-gaap:EarningsPerShareDiluted", "3.06", start, end, u="USD/shares"),
           fact("us-gaap:Assets", 2777687 * 10**6, i=end)]
    lines = ["Liabilities", "StockholdersEquity", "CashAndCashEquivalentsAtCarryingValue",
             "LiabilitiesCurrent", "AssetsCurrent", "InventoryNet", "AccountsPayableCurrent",
             "LongTermDebtNoncurrent", "ShortTermInvestments", "AccountsReceivableNetCurrent"]
    out += [fact(f"us-gaap:{t}", (k + 1) * 10**6, i=end) for k, t in enumerate(lines[:n - 4])]
    return out


def gold(conn, metric, end, company=CO):
    return conn.execute(
        "SELECT value, source_tag, accession_no FROM gold.quarterly_financials"
        " WHERE company_id = %s AND metric = %s AND period_end = %s",
        (company, metric, end)).fetchone()


def silver(conn, accession):
    return conn.execute(
        "SELECT source, count(*) FROM silver.financial_facts WHERE accession_no = %s"
        " GROUP BY 1 ORDER BY 1", (accession,)).fetchall()


def run(conn, fetch=None, **kw):
    kw.setdefault("since", date(2019, 1, 1))
    return filings.run(conn, fetch, **kw)


@db
def test_a_filing_companyfacts_never_received_reaches_gold_from_its_extract(conn):
    # Citigroup's Q1 2026 10-Q: extracted, never in companyfacts
    stored(conn, "c-q1", CO, statements())
    stats = run(conn)
    assert stats.filings == 1 and stats.sec_requests == 0
    assert gold(conn, "revenue", "2026-03-31") == (Decimal("24633000000"), "us-gaap:Revenues",
                                                   "c-q1")
    assert gold(conn, "eps_diluted", "2026-03-31")[0] == Decimal("3.06")
    assert silver(conn, "c-q1") == [("filing", 12)]
    row = conn.execute("SELECT source, gap, document FROM bronze.filing_facts"
                       " WHERE accession_no = 'c-q1'").fetchone()
    assert row == ("ixbrl", True, "https://www.sec.gov/q.htm")
    # idempotent: a second run reads nothing new and changes nothing
    assert run(conn).filings == 0
    assert silver(conn, "c-q1") == [("filing", 12)]


@db
def test_when_companyfacts_catches_up_its_copy_replaces_the_filings_own(conn):
    stored(conn, "c-q1", CO, statements())
    run(conn)
    payload = {"cik": int(CO), "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": "2026-01-01", "end": "2026-03-31", "val": 24633000000, "accn": "c-q1",
             "form": "10-Q", "filed": "2026-05-07"}]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2026-01-01", "end": "2026-03-31", "val": 5785000000, "accn": "c-q1",
             "form": "10-Q", "filed": "2026-05-07"}]}}}}}
    from facts_fetcher.db import refresh_silver, store_payload
    store_payload(conn, CO, json.dumps(payload).encode())
    refresh_silver(conn, [CO])  # identical keys conflict with the filing's own rows: skipped
    assert dict(silver(conn, "c-q1"))["filing"] == 12
    stats = run(conn)
    assert stats.caught_up == 1
    assert silver(conn, "c-q1") == [("companyfacts", 2)]  # never both, never twice
    assert gold(conn, "revenue", "2026-03-31")[0] == Decimal("24633000000")
    assert gold(conn, "total_assets", "2026-03-31") is None  # companyfacts' copy is what stays
    # superseded: the gap list doesn't take it back
    assert run(conn).filings == 0


@db
def test_a_filing_whose_document_lacks_the_statements_is_read_from_its_instance(conn):
    # Clorox's FY2026 10-K: the primary document has the cover page only
    conn.execute("UPDATE silver.company_ciks SET cik = '0000021076' WHERE company_id = %s", (CO,))
    stored(conn, "clx-10k", "0000021076",
           [fact("dei:EntityPublicFloat", 12200000000, i="2025-12-31")], form="10-K",
           filed=date(2026, 8, 7))
    asked = []

    def fetch(cik, accession):
        asked.append((cik, accession))
        return ("https://www.sec.gov/Archives/edgar/data/21076/000002107626000034/"
                "clx-20260630_htm.xml", read("clx-20260630_htm.xml.gz"))

    stats = run(conn, fetch)
    assert asked == [("0000021076", "clx-10k")] and stats.instances_fetched == 1
    got = conn.execute(
        "SELECT value FROM gold.derived_financials WHERE company_id = %s AND span = 'ttm'"
        " AND metric = 'revenue' AND period_end = '2026-06-30'", (CO,)).fetchone()
    assert got == (Decimal("6720000000"),)
    assert conn.execute("SELECT source FROM bronze.filing_facts WHERE accession_no = 'clx-10k'"
                        ).fetchone() == ("instance",)
    assert conn.execute("SELECT count(*) FROM bronze.filing_instances"
                        " WHERE accession_no = 'clx-10k'").fetchone() == (1,)
    run(conn, fetch)
    assert len(asked) == 1  # stored once, never fetched again


@db
def test_nvidia_capex_extension_fills_the_fiscal_fourth_quarter(conn):
    # migration 055's row: capex <- nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets,
    # NVIDIA's only.
    # the 10-Q's nine months under a us-gaap tag, as companyfacts has it
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, 'capex',"
        " 'PaymentsToAcquirePropertyPlantAndEquipment', 'USD', '2018-01-29', '2018-10-28',"
        " 397000000, 'q3', '10-Q', '2018-11-15')", (NVDA,))
    index(conn, "fy20", [NVDA], form="10-K", filed=date(2020, 2, 20))
    conn.execute(  # companyfacts has the 10-K (other tags)
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, 'revenue', 'Revenues', 'USD',"
        " '2019-01-28', '2020-01-26', 10918000000, 'fy20', '10-K', '2020-02-20')", (NVDA,))
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581020000010/nvda-2020x10k_htm.xml"
    stats = run(conn, lambda cik, a: (url, read("nvda-2020x10k_htm.xml.gz")), accessions=["fy20"])
    assert stats.filings == 1
    assert silver(conn, "fy20") == [("companyfacts", 1), ("filing_extra", 3)]
    q4 = gold(conn, "capex", "2019-01-27", NVDA)
    assert q4 == (Decimal("203000000"),
                  "nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets", "fy20")
    # another company's filing never reads NVIDIA's tag
    assert project([fact("nvda:PurchasesOfPropertyAndEquipmentAndIntangibleAssets", 1, "a", "b")],
                   "other", filings.mapping(conn), True) == []


def ford(conn, members, total=None, axis=SEG, extra=None):
    conn.execute("INSERT INTO silver.member_sums (company_id, tag, concept, axis, members)"
                 " VALUES (%s, 'DebtCurrent[f:A+f:B]', 'us-gaap:DebtCurrent', %s, '{f:A,f:B}')",
                 (CO, axis))
    conn.execute("INSERT INTO silver.metric_tags (metric, taxonomy, tag, priority, company_id)"
                 " VALUES ('long_term_debt_current', 'us-gaap', 'DebtCurrent[f:A+f:B]', 99, %s)",
                 (CO,))
    facts = statements(end="2025-12-31", start="2025-10-01")
    facts += [fact("us-gaap:DebtCurrent", v, i="2025-12-31", m={axis: mem, **(extra or {})})
              for mem, v in members.items()]
    if total is not None:
        facts.append(fact("us-gaap:DebtCurrent", total, i="2025-12-31"))
    stored(conn, "f-10k", CO, facts, form="10-K", filed=date(2026, 2, 5))
    conn.execute(  # companyfacts has the 10-K, without the debt lines
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, 'total_assets', 'Assets', 'USD', NULL,"
        " '2025-12-31', 289160000000, 'f-10k', '10-K', '2026-02-05')", (CO,))
    run(conn)
    return gold(conn, "long_term_debt_current", "2025-12-31")


@db
def test_debt_filed_only_by_segment_is_the_sum_of_every_member(conn):
    got = ford(conn, {"f:A": 5550 * 10**6, "f:B": 51752 * 10**6})
    assert got == (Decimal("57302000000"), "us-gaap:DebtCurrent[f:A+f:B]", "f-10k")


@db
def test_operating_segments_consolidation_member_is_no_second_dimension(conn):
    got = ford(conn, {"f:A": 730, "f:B": 49434},
               extra={"srt:ConsolidationItemsAxis": "us-gaap:OperatingSegmentsMember"})
    assert got[0] == Decimal("50164")


@db
def test_no_sum_when_a_member_is_missing(conn):
    assert ford(conn, {"f:A": 5550}) is None


@db
def test_no_sum_when_another_member_is_on_the_axis(conn):
    assert ford(conn, {"f:A": 5550, "f:B": 51752, "f:C": 1}) is None


@db
def test_no_sum_when_the_filing_has_the_consolidated_figure(conn):
    assert ford(conn, {"f:A": 5550, "f:B": 51752}, total=57302) is None
