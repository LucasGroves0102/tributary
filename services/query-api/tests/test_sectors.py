"""The sector mapping (db/migrations/046_sectors.sql): ranges never overlap, every SIC code the SEC
had assigned to a company (fixtures/sic_codes.csv, 2026-11-09) falls in exactly one, and the
header's counts are true."""

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (ROOT / "db" / "migrations" / "046_sectors.sql").read_text()
ROWS = re.findall(r"\('(\d{4})', '(\d{4})', '([^']+)', '([^']+)'\)", MIGRATION)
CODES = list(csv.DictReader((Path(__file__).parent / "fixtures" / "sic_codes.csv").open()))


def sector_of(sic: str) -> list[tuple[str, str]]:
    return [(s, i) for lo, hi, s, i in ROWS if lo <= sic <= hi]


def test_ranges_are_ordered_and_never_overlap():
    assert len(ROWS) > 100
    spans = sorted((lo, hi) for lo, hi, _, _ in ROWS)
    for lo, hi in spans:
        assert lo <= hi
    for (_, hi), (lo, _) in zip(spans, spans[1:], strict=False):
        assert hi < lo, f"{hi} overlaps {lo}"


def test_every_assigned_sic_has_exactly_one_sector():
    assert len(CODES) == 399
    missing = [c["sic"] for c in CODES if len(sector_of(c["sic"])) != 1]
    assert missing == []


def test_header_counts():
    sectors = {s for _, _, s, _ in ROWS}
    industries = {i for _, _, _, i in ROWS}
    m = re.search(r"into (\d+) sectors and (\d+) industries", MIGRATION)
    assert m and (int(m[1]), int(m[2])) == (len(sectors), len(industries))
    assert "399 of them" in MIGRATION


def test_judgment_calls_land_where_the_header_says():
    assert sector_of("3674") == [("Technology", "Semiconductors")]
    assert sector_of("3679") == [("Technology", "Electronic components")]       # Vertiv
    assert sector_of("3021") == [("Consumer Discretionary", "Apparel, footwear & accessories")]
    assert sector_of("6021") == [("Financials", "Banks & thrifts")]
    assert sector_of("6324") == [("Health Care", "Managed health care")]
    assert sector_of("6770") == [("Funds & Vehicles", "Blank checks (SPACs)")]
    assert sector_of("3533")[0][0] == sector_of("4922")[0][0] == "Energy"
    assert sector_of("5331") == [("Consumer Staples", "Food & general merchandise retail")]
    assert sector_of("0000") == []


def test_industries_belong_to_one_sector():
    owner: dict[str, set[str]] = {}
    for _, _, s, i in ROWS:
        owner.setdefault(i, set()).add(s)
    assert {i: s for i, s in owner.items() if len(s) > 1} == {}
