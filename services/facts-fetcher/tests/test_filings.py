"""A filing's own XBRL where companyfacts doesn't carry it (filings.py, instance.py, migrations 055
and 073's member picks).

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


RWA = "us-gaap:RiskWeightedAssetsCalculationMethodologyAxis"
PARENT = {"srt:ConsolidatedEntitiesAxis": "srt:ParentCompanyMember"}
STANDARDIZED = {**PARENT, RWA: "jpm:BaselIIIStandardizedMember"}


def test_a_pick_keeps_only_facts_with_exactly_its_dimensions():
    # JPMorgan's CET1 ratio (073): the parent's standardized ratio, not the advanced approach, not
    # the bank subsidiary, not a fact with one axis more or less
    m = Mapping(standard=frozenset(), own={}, sums={},
                picks={"J": {"jpm:Cet1": [STANDARDIZED]}})
    facts = [fact("jpm:Cet1", "0.14", i="2026-09-30", u="pure", m=STANDARDIZED),
             fact("jpm:Cet1", "0.153", i="2026-09-30", u="pure",
                  m={**PARENT, RWA: "jpm:BaselIIIAdvancedMember"}),
             fact("jpm:Cet1", "0.142", i="2026-09-30", u="pure",
                  m={"dei:LegalEntityAxis": "jpm:JpmorganChaseBankNAMember",
                     RWA: "jpm:BaselIIIStandardizedMember"}),
             fact("jpm:Cet1", "0.1", i="2026-09-30", u="pure",
                  m={**STANDARDIZED, "srt:ProductOrServiceAxis": "x"}),
             fact("jpm:Cet1", "0.2", i="2026-09-30", u="pure", m={RWA: STANDARDIZED[RWA]})]
    assert [f["v"] for f in project(facts, "J", m, gap=False)] == ["0.14"]
    assert project(facts, "other", m, gap=True) == []


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


JPM = "0000019617"


def jpm_q3(conn, cet1):
    """JPMorgan's Q3 2026 10-Q as stored: statements companyfacts has, and the CET1 ratios it
    files only with dimensions (values from its extract on hermes)."""
    facts = statements(start="2026-07-01", end="2026-09-30") + [
        fact("jpm:CommonEquityTier1CapitaltoRiskWeightedAssets", v, i="2026-09-30", u="pure",
             m={**PARENT, RWA: member})
        for member, v in cet1.items()]
    stored(conn, "jpm-q3", JPM, facts, filed=date(2026, 11, 3))
    conn.execute(  # companyfacts has the filing (other tags): not a gap
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, 'total_assets', 'Assets', 'USD', NULL,"
        " '2026-09-30', 4561000000000, 'jpm-q3', '10-Q', '2026-11-03')", (JPM,))


@db
def test_jpmorgans_standardized_cet1_reaches_gold_from_its_pick(conn):
    # 073's own rows: the parent company's standardized ratio, never the advanced one
    jpm_q3(conn, {"jpm:BaselIIIStandardizedMember": "0.14", "jpm:BaselIIIAdvancedMember": "0.153"})
    stats = run(conn)
    assert stats.filings == 1 and stats.sec_requests == 0
    got = gold(conn, "cet1_ratio", "2026-09-30", JPM)
    assert got == (Decimal("0.14"), "jpm:CommonEquityTier1CapitaltoRiskWeightedAssets"
                   "[srt:ParentCompanyMember+jpm:BaselIIIStandardizedMember]", "jpm-q3")
    assert silver(conn, "jpm-q3") == [("companyfacts", 1), ("filing_extra", 1)]
    assert run(conn).filings == 0  # read once


@db
def test_the_older_standardized_names_fill_in_and_the_current_one_wins(conn):
    jpm_q3(conn, {"jpm:BaselIIIStandardizedTransitionalMember": "0.12",
                  "jpm:BaselIIIStandardizedMember": "0.14"})
    run(conn)
    assert gold(conn, "cet1_ratio", "2026-09-30", JPM)[0] == Decimal("0.14")


@db
def test_a_pick_added_later_rereads_the_documents_already_read(conn):
    conn.execute("DELETE FROM silver.member_picks WHERE company_id = %s", (JPM,))
    jpm_q3(conn, {"jpm:BaselIIIStandardizedMember": "0.14"})
    conn.execute(  # read before the pick existed
        "INSERT INTO bronze.filing_facts (accession_no, cik, form, filed, source, document, gap,"
        " facts, fact_count, extractor_version, projected_at)"
        " VALUES ('jpm-q3', %s, '10-Q', '2026-11-03', 'ixbrl', 'q.htm', false, '[]', 15, %s,"
        " now() - interval '1 day')", (JPM, filings.EXTRACTOR_VERSION))
    assert run(conn).filings == 0
    conn.execute(
        "INSERT INTO silver.member_picks (company_id, tag, concept, members) VALUES (%s,"
        " 'CommonEquityTier1CapitaltoRiskWeightedAssets[srt:ParentCompanyMember"
        "+jpm:BaselIIIStandardizedMember]', 'jpm:CommonEquityTier1CapitaltoRiskWeightedAssets',"
        " %s)", (JPM, json.dumps(STANDARDIZED)))
    assert run(conn).filings == 1
    assert gold(conn, "cet1_ratio", "2026-09-30", JPM)[0] == Decimal("0.14")
    assert run(conn).filings == 0


@db
def test_the_shared_us_gaap_pick_reads_only_for_companies_that_have_it(conn):
    # Wells Fargo's and Bank of America's pick: one us-gaap tag (073), read for its companies only
    std = {**PARENT, RWA: "us-gaap:StandardizedApproachMember"}
    facts = statements(start="2026-07-01", end="2026-09-30") + [
        fact("us-gaap:CommonEquityTierOneCapitalRatio", "0.1003", i="2026-09-30", u="pure", m=std)]
    stored(conn, "co-q3", CO, facts, filed=date(2026, 10, 28))
    conn.execute(
        "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
        " value, accession_no, form, filed) VALUES (%s, 'total_assets', 'Assets', 'USD', NULL,"
        " '2026-09-30', 1, 'co-q3', '10-Q', '2026-10-28')", (CO,))
    run(conn)
    assert gold(conn, "cet1_ratio", "2026-09-30") is None  # Home Depot has no pick
    conn.execute(
        "INSERT INTO silver.member_picks (company_id, tag, concept, members) VALUES (%s,"
        " 'CommonEquityTierOneCapitalRatio[srt:ParentCompanyMember"
        "+us-gaap:StandardizedApproachMember]', 'us-gaap:CommonEquityTierOneCapitalRatio', %s)",
        (CO, json.dumps(std)))
    run(conn)
    assert gold(conn, "cet1_ratio", "2026-09-30")[0] == Decimal("0.1003")


COST, WMT = "0000909832", "0000104169"  # Costco: conftest's row (SIC 5331)
PRODUCT = {"srt:ProductOrServiceAxis": "us-gaap:ProductMember"}
MEMBERSHIP = {"srt:ProductOrServiceAxis": "us-gaap:MembershipMember"}
CONTRACT = "RevenueFromContractWithCustomerExcludingAssessedTax"


def companyfacts(conn, cik, accession, form, filed, start, end, lines):
    """Facts companyfacts has for the filing: (metric, tag, value)."""
    for metric, tag, value in lines:
        conn.execute(
            "INSERT INTO silver.financial_facts (cik, metric, tag, unit, period_start, period_end,"
            " value, accession_no, form, filed) VALUES (%s, %s, %s, 'USD', %s, %s, %s, %s, %s,"
            " %s)", (cik, metric, tag, start, end, value, accession, form, filed))


def margins(conn, company, span, end):
    return conn.execute(
        "SELECT metric, value, inputs FROM gold.derived_financials WHERE company_id = %s"
        " AND span = %s AND period_end = %s AND metric IN ('gross_margin', 'net_sales', 'revenue')"
        " ORDER BY 1", (company, span, end)).fetchall()


@db
def test_costcos_net_sales_come_from_its_pick_and_its_gross_margin_reads_them(conn):
    """084: Costco's FY2026 10-K files net sales only by product, beside membership fees;
    companyfacts has the totals (values from its extract on hermes)."""
    start, end = "2025-09-01", "2026-08-30"
    facts = statements(start=start, end=end, revenue=303154) + [
        fact(f"us-gaap:{CONTRACT}", 297247 * 10**6, start, end, m=PRODUCT),
        fact(f"us-gaap:{CONTRACT}", 5907 * 10**6, start, end, m=MEMBERSHIP),
        fact(f"us-gaap:{CONTRACT}", 303154 * 10**6, start, end),
        fact("us-gaap:CostOfGoodsAndServicesSold", 264279 * 10**6, start, end)]
    stored(conn, "cost-10k", COST, facts, form="10-K", filed=date(2026, 10, 7))
    companyfacts(conn, COST, "cost-10k", "10-K", "2026-10-07", start, end, [
        ("revenue", CONTRACT, 303154 * 10**6),
        ("cost_of_revenue", "CostOfGoodsAndServicesSold", 264279 * 10**6)])
    assert run(conn).filings == 1  # the pick's extras pass, no SEC request
    assert silver(conn, "cost-10k") == [("companyfacts", 2), ("filing_extra", 1)]
    got = {m: (v, i) for m, v, i in margins(conn, COST, "ttm", end)}
    assert got["revenue"][0] == 303154 * 10**6  # total revenue stays revenue
    assert got["net_sales"][0] == 297247 * 10**6
    assert got["gross_margin"] == (Decimal("0.110911"), {"net_sales": 297247 * 10**6,
                                                         "cost_of_revenue": 264279 * 10**6})


@db
def test_walmarts_net_sales_are_its_own_tag_and_other_retailers_stay_without_a_gross_margin(conn):
    """084: Walmart's quarter to Jul 31, 2026 (companyfacts, values from hermes): net sales
    $186,100M of total revenues $187,937M, cost of sales $138,804M: 25.4%, and (155) a gross
    profit of $47,296M on net sales. A variety store without net sales read apart keeps 039's
    rule: no gross profit, no gross margin."""
    start, end = "2026-05-01", "2026-07-31"
    other = "0000027419"  # Target's CIK, SIC 5331
    for cid, name in ((WMT, "Walmart Inc."), (other, "Target")):
        conn.execute("INSERT INTO silver.companies (company_id, name, current_cik, tracked, sic)"
                     " VALUES (%s, %s, %s, true, '5331')", (cid, name, cid))
        conn.execute("INSERT INTO silver.company_ciks (cik, company_id) VALUES (%s, %s)",
                     (cid, cid))
    companyfacts(conn, WMT, "wmt-q2", "10-Q", "2026-08-28", start, end, [
        ("revenue", "Revenues", 187937 * 10**6), ("revenue", CONTRACT, 186100 * 10**6),
        ("cost_of_revenue", "CostOfRevenue", 138804 * 10**6)])
    companyfacts(conn, other, "tgt-q2", "10-Q", "2026-08-28", start, end, [
        ("revenue", "Revenues", 25000 * 10**6), ("revenue", CONTRACT, 24500 * 10**6),
        ("cost_of_revenue", "CostOfRevenue", 18000 * 10**6)])
    conn.execute("SELECT gold.refresh_financials(%s)", ([WMT, other],))
    assert gold(conn, "revenue", end, WMT)[:2] == (187937 * 10**6, "us-gaap:Revenues")
    assert gold(conn, "net_sales", end, WMT)[:2] == (186100 * 10**6, f"us-gaap:{CONTRACT}")
    # 155: gross profit on net sales where the company reports them (Morgan, test 11, finding 9);
    # the variety store without them stays without one (039)
    assert gold(conn, "gross_profit", end, WMT)[:2] == (47296 * 10**6,
                                                       "derived:net_sales-cost_of_revenue")
    assert gold(conn, "gross_profit", end, other) is None
    assert margins(conn, WMT, "quarter", end) == [
        ("gross_margin", Decimal("0.254143"), {"net_sales": 186100 * 10**6,
                                               "cost_of_revenue": 138804 * 10**6})]
    assert gold(conn, "net_sales", end, other) is None
    assert margins(conn, other, "quarter", end) == []


class _Spy:
    """A real connection that notes the transaction state each `transaction()` starts from."""

    def __init__(self, conn):
        self._conn, self.starts = conn, []

    def transaction(self, *a, **kw):
        self.starts.append(self._conn.info.transaction_status.name)
        return self._conn.transaction(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@db
def test_rebuild_commits_each_batch_instead_of_holding_one_transaction():
    # mapping() ran outside any transaction and opened an implicit one, so every batch's
    # transaction() was a savepoint and --rebuild held one transaction for the whole job
    import psycopg

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as c:
        if c.execute("SELECT count(*) FROM bronze.filing_facts").fetchone()[0]:
            pytest.skip("bronze.filing_facts has rows: this test must not rebuild them")
        c.rollback()
        spy = _Spy(c)
        filings.rebuild(spy, filings.RunStats())
        assert spy.starts and set(spy.starts) == {"IDLE"}
        assert c.info.transaction_status.name == "IDLE"


# foreign annual reports (roadmap f) ------------------------------------------------------------

TSMC = "0001046179"


def ifrs_instance(cik: str, year: int, revenue: int, usd_revenue: int) -> bytes:
    """An inline 20-F's instance shaped like TSMC's: IFRS facts in New Taiwan dollars, the
    convenience translation of the year in US dollars beside them, two years of the income
    statement, both balance sheets (values made up, at TSMC's scale)."""
    cur, prior = (f"{year}-01-01", f"{year}-12-31"), (f"{year - 1}-01-01", f"{year - 1}-12-31")
    facts = [("Revenue", "y", "TWD", revenue), ("Revenue", "p", "TWD", revenue * 3 // 4),
             ("Revenue", "y", "USD", usd_revenue),
             ("ProfitLossAttributableToOwnersOfParent", "y", "TWD", revenue * 45 // 100),
             ("ProfitLossFromOperatingActivities", "y", "TWD", revenue // 2),
             ("CashFlowsFromUsedInOperatingActivities", "y", "TWD", revenue * 6 // 10),
             ("Assets", "e", "TWD", revenue * 2), ("Assets", "pe", "TWD", revenue * 3 // 2),
             ("Liabilities", "e", "TWD", revenue * 2 // 3),
             ("EquityAttributableToOwnersOfParent", "e", "TWD", revenue * 4 // 3),
             ("CashAndCashEquivalents", "e", "TWD", revenue // 2)]
    contexts = {"y": f"<x:startDate>{cur[0]}</x:startDate><x:endDate>{cur[1]}</x:endDate>",
                "p": f"<x:startDate>{prior[0]}</x:startDate><x:endDate>{prior[1]}</x:endDate>",
                "e": f"<x:instant>{cur[1]}</x:instant>", "pe": f"<x:instant>{prior[1]}</x:instant>"}
    out = ['<?xml version="1.0"?><x:xbrl xmlns:x="http://www.xbrl.org/2003/instance"'
           ' xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full"'
           ' xmlns:dei="http://xbrl.sec.gov/dei/2025"'
           ' xmlns:iso4217="http://www.xbrl.org/2003/iso4217">']
    for cid, period in contexts.items():
        out.append(f'<x:context id="{cid}"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">'
                   f'{cik}</x:identifier></x:entity><x:period>{period}</x:period></x:context>')
    for unit in ("TWD", "USD"):
        out.append(f'<x:unit id="{unit}"><x:measure>iso4217:{unit}</x:measure></x:unit>')
    out.append(f'<dei:EntityCentralIndexKey contextRef="y">{cik}</dei:EntityCentralIndexKey>')
    for tag, cid, unit, v in facts:
        out.append(f'<ifrs-full:{tag} contextRef="{cid}" unitRef="{unit}" decimals="-6">{v}'
                   f'</ifrs-full:{tag}>')
    return ("".join(out) + "</x:xbrl>").encode()


@db
def test_a_20f_companyfacts_lacks_reaches_gold_in_its_own_currency(conn):
    # TSMC's FY2025 20-F (filed 2026-04-16): seven months on, companyfacts still ended at FY2024
    conn.execute("UPDATE silver.company_ciks SET cik = %s WHERE company_id = %s", (TSMC, CO))
    conn.execute("UPDATE silver.companies SET sic = '3674' WHERE company_id = %s", (CO,))
    index(conn, "tsm-fy25", [TSMC], form="20-F", filed=date(2026, 4, 16))
    index(conn, "tsm-fy25-a", [TSMC], form="20-F/A", filed=date(2026, 5, 20))
    cover = (b'<?xml version="1.0"?><x:xbrl xmlns:x="http://www.xbrl.org/2003/instance"'
             b' xmlns:dei="http://xbrl.sec.gov/dei/2025"><x:context id="c"><x:entity>'
             b'<x:identifier scheme="http://www.sec.gov/CIK">0001046179</x:identifier></x:entity>'
             b'<x:period><x:instant>2026-05-20</x:instant></x:period></x:context>'
             b'<dei:AmendmentFlag contextRef="c">true</dei:AmendmentFlag></x:xbrl>')
    asked = []

    def fetch(cik, accession):
        asked.append(accession)
        return (f"https://www.sec.gov/Archives/edgar/data/1046179/{accession}/tsm-20251231_htm.xml",
                ifrs_instance(TSMC, 2025, 3_809_000_000_000, 122_400_000_000)
                if accession == "tsm-fy25" else cover)

    stats = run(conn, fetch, since=date(2025, 10, 1))
    assert sorted(asked) == ["tsm-fy25", "tsm-fy25-a"] and stats.sec_requests == 0  # injected
    revenue = conn.execute(
        "SELECT value, unit, source_tag FROM gold.financials WHERE company_id = %s"
        " AND span = 'ttm' AND metric = 'revenue' AND period_end = '2025-12-31'", (CO,)).fetchone()
    assert revenue == (Decimal("3809000000000"), "TWD", "ifrs-full:Revenue")  # never converted
    assert conn.execute(
        "SELECT unit FROM gold.financials WHERE company_id = %s AND metric = 'total_assets'"
        " AND period_end = '2025-12-31'", (CO,)).fetchone() == ("TWD",)
    assert dict(silver(conn, "tsm-fy25"))["filing"] >= 10
    assert silver(conn, "tsm-fy25-a") == []  # a cover-page amendment maps no fact
    assert run(conn, fetch, since=date(2025, 10, 1)).filings == 0 and len(asked) == 2


@db
def test_a_10k_amendment_is_still_no_gap(conn):
    index(conn, "k-a", [CO], form="10-K/A", filed=date(2026, 3, 1))
    assert filings.gaps(conn, date(2025, 10, 1), 3, 10) == []
