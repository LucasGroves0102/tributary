"""Inline XBRL extraction, labels and projection on real filings: NVIDIA's full 10-K and 10-Q
(the parser's fixtures) and trimmed Apple, Coca-Cola and PepsiCo documents
(tests/fixtures/ixbrl, made with trim_ixbrl.py from the SEC's copies)."""

import gzip
from decimal import Decimal
from pathlib import Path

import pytest

from facts_fetcher.ixbrl import extract_chunks, extract_gzip, parse_value, plain
from facts_fetcher.labels import member_label, words
from facts_fetcher.segments import build_row, decompress, fact_dict, project

HERE = Path(__file__).parent
PARSER = HERE.parents[1] / "parser" / "tests" / "fixtures"
IXBRL = HERE / "fixtures" / "ixbrl"
AXES = {"us-gaap:StatementBusinessSegmentsAxis", "srt:StatementGeographicalAxis",
        "srt:ProductOrServiceAxis", "srt:ConsolidationItemsAxis"}
PRODUCT = "srt:ProductOrServiceAxis"
SEGMENT = "us-gaap:StatementBusinessSegmentsAxis"
GEO = "srt:StatementGeographicalAxis"
OPERATING = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")


def facts(path: Path):
    return extract_gzip(path.read_bytes())


def value(ex, concept, start, end, *members):
    found = [f.value for f in ex.facts if f.concept == concept and f.start == start
             and f.end == end and f.members == tuple(sorted(members))]
    assert len(found) == 1, found
    return found[0]


# values -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,fmt,scale,sign,expected", [
    ("193,737", "ixt:num-dot-decimal", "6", None, "193737000000"),
    ("1,234.5", "ixt:numdotdecimal", None, None, "1234.5"),
    ("1.234,5", "ixt:num-comma-decimal", "3", None, "1234500"),
    ("42,627", "ixt:num-dot-decimal", "6", "-", "-42627000000"),
    ("—", "ixt:fixed-zero", "6", None, "0"),
    ("-", "ixt-sec:numdash", None, None, "0"),
    ("0.25", None, None, None, "0.25"),
    ("2.62", "ixt:num-dot-decimal", "0", None, "2.62"),
])
def test_parse_value(text, fmt, scale, sign, expected):
    assert plain(parse_value(text, fmt, scale, sign)) == expected


@pytest.mark.parametrize("text,fmt", [("three", "ixt-sec:numwordsen"), ("n/a", None),
                                      ("", "ixt:num-dot-decimal")])
def test_unsupported_values(text, fmt):
    assert parse_value(text, fmt, None, None) is None


def test_plain_is_exact():
    assert plain(Decimal("1.2E+10")) == "12000000000"
    assert plain(Decimal("0.0500")) == "0.05"


DOC = """<html><body>
<ix:nonFraction name="us-gaap:Revenues" contextRef="c2" unitRef="usd" scale="6"
  decimals="-6" format="ixt:num-dot-decimal">1,0<b>00</b></ix:nonFraction>
<ix:nonFraction name="us-gaap:Revenues" contextRef="c2" unitRef="usd" scale="6"
  format="ixt:num-dot-decimal">1,000</ix:nonFraction>
<ix:nonFraction name="us-gaap:Revenues" contextRef="c3" unitRef="usd" scale="6"
  format="ixt:num-dot-decimal">7</ix:nonFraction>
<ix:nonFraction name="us-gaap:Revenues" contextRef="c3" unitRef="usd" scale="6"
  format="ixt:num-dot-decimal">8</ix:nonFraction>
<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="c1" unitRef="eps"
  xsi:nil="true"/>
<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="c1" unitRef="eps"
  decimals="2">2.50</ix:nonFraction>
<ix:nonFraction name="x:Thing" contextRef="nope" unitRef="usd">1</ix:nonFraction>
<div style="display:none"><ix:header><ix:resources>
<xbrli:context id="c1"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c2"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>
<xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">x:WidgetsMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c3"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>
<xbrli:segment><xbrldi:typedMember dimension="x:StoreAxis"><x:id> 17 </x:id></xbrldi:typedMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
<xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
</ix:resources></ix:header></div></body></html>"""


def test_header_after_facts_nested_markup_duplicates_and_units():
    # fed in small chunks, so tags and text are split across feeds
    ex = extract_chunks(DOC[i:i + 7] for i in range(0, len(DOC), 7))
    by = {(f.concept, f.members): f for f in ex.facts}
    widgets = by[("us-gaap:Revenues", (("srt:ProductOrServiceAxis", "x:WidgetsMember"),))]
    assert (widgets.value, widgets.unit, widgets.decimals) == (Decimal(1000000000), "USD", "-6")
    assert (widgets.start, widgets.end, widgets.instant) == ("2026-01-01", "2026-03-31", None)
    store = by[("us-gaap:Revenues", (("x:StoreAxis", "typed:17"),))]
    assert (store.value, store.instant) == (Decimal(7000000), "2026-03-31")
    eps = by[("us-gaap:EarningsPerShareDiluted", ())]
    assert (eps.value, eps.unit) == (Decimal("2.50"), "USD/shares")
    assert len(ex.facts) == 3
    assert (ex.conflicts, ex.missing_context, ex.entity_cik) == (1, 1, "0000000001")


# real filings --------------------------------------------------------------------------------

def test_nvidia_10k_segments_products_and_geographies():
    ex = facts(PARSER / "nvda-10-K.htm.gz")
    fy = ("2025-01-27", "2026-01-25")
    assert ex.entity_cik == "0001045810" and ex.has_header
    assert value(ex, "us-gaap:Revenues", *fy) == Decimal("215938000000")
    assert value(ex, "us-gaap:Revenues", *fy, (PRODUCT, "nvda:DataCenterMember")) \
        == Decimal("193737000000")
    assert value(ex, "us-gaap:Revenues", *fy, (PRODUCT, "nvda:GamingMember")) \
        == Decimal("16042000000")
    assert value(ex, "us-gaap:Revenues", *fy, (GEO, "country:TW")) == Decimal("42345000000")
    assert value(ex, "us-gaap:OperatingIncomeLoss", *fy, OPERATING,
                 (SEGMENT, "nvda:GraphicsSegmentMember")) == Decimal("9156000000")


def test_nvidia_10q_has_quarter_and_year_to_date():
    ex = facts(PARSER / "nvda-10-Q.htm.gz")
    member = (PRODUCT, "nvda:DataCenterMember")
    assert value(ex, "us-gaap:Revenues", "2026-04-27", "2026-07-26", member) \
        == Decimal("89023000000")
    assert value(ex, "us-gaap:Revenues", "2026-01-26", "2026-07-26", member) \
        == Decimal("164269000000")


def test_apple_products_and_geographic_segments():
    ex = facts(IXBRL / "aapl-10-K-2025.htm.gz")
    fy = ("2024-09-29", "2025-09-27")
    tag = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    assert value(ex, tag, *fy, (PRODUCT, "aapl:IPhoneMember")) == Decimal("209586000000")
    assert value(ex, tag, *fy, (PRODUCT, "us-gaap:ServiceMember")) == Decimal("109158000000")
    assert value(ex, tag, *fy, OPERATING, (SEGMENT, "aapl:GreaterChinaSegmentMember")) \
        == Decimal("64377000000")
    assert value(ex, "us-gaap:OperatingIncomeLoss", *fy,
                 ("srt:ConsolidationItemsAxis", "us-gaap:CorporateNonSegmentMember")) \
        == Decimal("-42627000000")


def test_coca_cola_and_pepsico_segments():
    ko = facts(IXBRL / "ko-10-K-2025.htm.gz")
    assert value(ko, "us-gaap:Revenues", "2025-01-01", "2025-12-31", OPERATING,
                 (SEGMENT, "ko:NorthAmericaSegmentMember")) == Decimal("19586000000")
    pep = facts(IXBRL / "pep-10-Q-2025Q3.htm.gz")
    assert value(pep, "us-gaap:Revenues", "2025-06-15", "2025-09-06", OPERATING,
                 (SEGMENT, "pep:PepsiCoBeveragesNorthAmericaMember")) == Decimal("7327000000")
    assert value(pep, "us-gaap:Revenues", "2024-12-29", "2025-09-06") == Decimal("64582000000")


# labels --------------------------------------------------------------------------------------

@pytest.mark.parametrize("qname,label", [
    ("nvda:DataCenterMember", "Data Center"),
    ("nvda:GamingMember", "Gaming"),
    ("nvda:OEMAndOtherMember", "OEM and Other"),
    ("nvda:ComputeAndNetworkingSegmentMember", "Compute and Networking"),
    ("nvda:ChinaIncludingHongKongMember", "China including Hong Kong"),
    ("srt:AmericasMember", "Americas"),
    ("us-gaap:AmericasMember", "Americas"),
    ("aapl:IPhoneMember", "iPhone"),
    ("aapl:IPadMember", "iPad"),
    ("aapl:WearablesHomeandAccessoriesMember", "Wearables, Home and Accessories"),
    ("aapl:GreaterChinaSegmentMember", "Greater China"),
    ("aapl:RestOfAsiaPacificSegmentMember", "Rest of Asia Pacific"),
    ("ko:A.PacificMember", "Asia Pacific"),
    ("ko:BottlingInvestmentsMember", "Bottling Investments"),
    ("pep:PepsiCoBeveragesNorthAmericaMember", "PepsiCo Beverages North America"),
    ("pep:LatinAmericaFoodsSegmentMember", "Latin America Foods"),
    ("us-gaap:NonUsMember", "Non-US"),
    ("us-gaap:CorporateNonSegmentMember", "Corporate"),
    ("country:TW", "Taiwan"),
    ("country:ZZ", "ZZ"),
    ("stpr:TX", "Texas"),
    ("x:ITServicesMember", "IT Services"),
    ("x:Segment1Member", "Segment 1"),
    ("x:SegmentMember", "Segment"),
    ("x:typed:17", "17"),
])
def test_member_labels(qname, label):
    assert member_label(qname) == label


def test_words_keeps_acronyms_and_lowercases_joining_words():
    assert words("USAndCanada") == "US and Canada"
    assert words("AsiaPacificExcludingJapan") == "Asia Pacific excluding Japan"


# projection ----------------------------------------------------------------------------------

def test_projection_keeps_segment_facts_and_their_totals():
    ex = facts(PARSER / "nvda-10-K.htm.gz")
    p = project([fact_dict(f) for f in ex.facts], AXES)
    dims = [f for f in p.facts if f.get("m")]
    assert dims and all(set(f["m"]) <= AXES and len(f["m"]) <= 2 for f in dims)
    concepts = {f["c"] for f in dims}
    totals = [f for f in p.facts if not f.get("m")]
    assert {f["c"] for f in totals} <= concepts
    assert any(f["c"] == "us-gaap:Revenues" and f.get("e") == "2026-01-25" for f in totals)
    assert p.labels["nvda:DataCenterMember"] == "Data Center"
    assert p.labels["country:US"] == "United States"
    assert len(p.facts) < len(ex.facts) / 2


def test_build_row_stores_everything_raw_and_the_projection():
    raw = (IXBRL / "pep-10-K-2025.htm.gz").read_bytes()
    row = build_row("0000077476-26-000007", "0000077476", "sha", raw, AXES)
    assert row.status == "ok" and row.entity_cik == "0000077476"
    assert row.raw_gzip is not None and len(decompress(row.raw_gzip)) == row.fact_count
    assert '"pep:PepsiCoFoodsNorthAmericaMember"' in row.labels_json


def test_build_row_without_inline_xbrl_or_broken():
    plain_html = gzip.compress(b"<html><body><p>Item 7. MD&amp;A</p></body></html>")
    assert build_row("a", "1", "s", plain_html, AXES).status == "no_ixbrl"
    broken = build_row("a", "1", "s", b"not gzip", AXES)
    assert broken.status == "error" and broken.error
