"""query_api.releases: outlook and adjustments found in real press releases (fixtures/releases,
public 8-K EX-99.1 texts) and in small texts for each rule."""

from pathlib import Path

import pytest

from query_api.releases import dates_only, is_dateline, is_heading, lines_of, read_release

RELEASES = Path(__file__).parent / "fixtures" / "releases"


def release(name: str) -> str:
    return (RELEASES / name).read_text(encoding="utf-8")


def headings(passages) -> list[str | None]:
    return [p.heading for p in passages]


@pytest.mark.parametrize("name", sorted(p.name for p in RELEASES.glob("*.txt")))
def test_every_passage_is_quoted_from_its_offsets(name):
    text = release(name)
    r = read_release(text)
    for p in [*([r.headline] if r.headline else []), *r.outlook, *r.adjustments]:
        quoted = [ln.text for ln in lines_of(text[p.start:p.end])]
        assert "\n".join(quoted) == p.text
        assert text[p.start:p.end].startswith(p.text.split("\n")[0])


def test_vertiv_guidance_tables_and_reconciliations():
    r = read_release(release("vrt-2026-10-28-ex99-1.txt"))
    assert r.headline.text.startswith("Vertiv Reports Strong Third Quarter 2026")
    assert headings(r.outlook) == [
        "Fourth Quarter 2026 Guidance", "Full Year 2026 Guidance",
        "Updated Full Year and Fourth Quarter 2026 Guidance",
        "Fourth Quarter 2026 Guidance", "Full Year 2026 Guidance"]
    q4 = r.outlook[3].text.split("\n")
    assert q4[1:3] == ["Net sales $4,313M - $4,513M", "Organic net sales growth(2)"]
    assert q4[-2:] == ["Adjusted diluted EPS growth(2)", "63% - 67%"]
    year = r.outlook[4].text
    assert "Adjusted free cash flow(2)\n$2,600M - $2,800M" in year
    assert "Conference Call" not in year  # the next section isn't outlook
    # the summary's bullets stop at the dateline that opens the release
    assert "COLUMBUS" not in r.outlook[1].text
    tables = [p for p in r.adjustments if p.kind == "reconciliation"]
    assert [p.heading for p in tables][1:4] == [
        "Reconciliation of net cash provided by (used for) operating activities to adjusted free"
        " cash flow",
        "Reconciliation from operating profit (loss) to adjusted operating profit (loss)",
        "Reconciliation from operating margin to adjusted operating margin"]
    assert tables[1].text.endswith("Adjusted free cash flow $ 652.2 $ 436.0 $ 2,230.3 $ 977.5")
    eps = tables[4].text
    assert "Non-GAAP adjusted $ 1,086.9 $ 6.4 $ (17.4) $ 230.3 $ 867.6 $ 2.21" in eps
    assert "Regional Segment Results" not in tables[0].text  # another table's title ends it
    assert not [p for p in r.adjustments if p.kind == "note"]


def test_deckers_outlook_and_the_refund_note():
    """Tester 2's facts: the FY27 EPS range, and the $46.0M of IEEPA refunds in the quarter."""
    r = read_release(release("deck-2026-10-22-ex99-1.txt"))
    assert r.headline.text == ("DECKERS BRANDS REPORTS SECOND QUARTER FISCAL YEAR 2027 FINANCIAL"
                               " RESULTS")
    [outlook] = r.outlook
    assert outlook.heading == ("Full Fiscal Year 2027 Outlook(2) for the Twelve Month Period"
                               " Ending March 31, 2027")
    assert "•Diluted earnings per share is now expected to be in the range of $7.80 to $7.95." \
        in outlook.text
    assert outlook.text.split("\n")[-1].startswith("(2) Through October 8, 2026")
    assert "Non-GAAP Financial Measures" not in outlook.text
    [note] = r.adjustments  # no reconciliation table; the (2) note is quoted with the outlook
    assert note.kind == "note" and note.heading is None
    assert note.text.startswith("(1) During the second quarter of fiscal year 2027")
    assert "$46.0 million" in note.text


def test_nvidia_outlook_stops_at_highlights_and_tables_keep_their_rows():
    r = read_release(release("nvda-2026-08-26-ex99-1.txt"))
    [outlook] = r.outlook
    lines = outlook.text.split("\n")
    assert lines[0] == "Outlook"
    assert lines[2].startswith("•Revenue is expected to be $108.0 billion")
    assert lines[-1].startswith("For the full year fiscal 2027, NVIDIA expects")
    assert headings(r.adjustments) == ["RECONCILIATION OF GAAP TO NON-GAAP FINANCIAL MEASURES",
                                       "RECONCILIATION OF GAAP TO NON-GAAP OUTLOOK"]
    first = r.adjustments[0].text
    assert "Free cash flow $ 21,341 $ 48,554 $ 13,450 $ 69,895 $ 39,584" in first
    assert "24,285 24,391 24,532 24,338 24,571" in first  # a row label's values, not a title
    assert not first.endswith("NVIDIA CORPORATION")  # the next table's company line is dropped
    assert "Q3 FY2027 Outlook" in r.adjustments[1].text  # inside a reconciliation, not outlook


def test_funko_table_outlook_and_a_stated_heading():
    r = read_release(release("fnko-2026-11-05-ex99-1.txt"))
    assert headings(r.outlook) == ["Reiterates Full-Year Net Sales and Adjusted EBITDA Guidance",
                                   "Outlook for 2026", "Current Outlook"]
    assert r.outlook[2].text.split("\n")[2] == "Net Sales Reiterating flat to up 3%"


def test_a_release_without_outlook_gets_none():
    r = read_release(release("phm-2026-10-22-ex99-1.txt"))
    assert r.outlook == []
    assert r.headline is not None and not r.headline.text.startswith("Investors:")


def test_heading_and_dateline_rules():
    assert is_heading("Full Year 2026 Guidance")
    assert not is_heading("Net sales $4,313M - $4,513M")
    assert not is_heading("•Expects fourth quarter net sales")
    assert not is_heading("The company expects:")
    assert is_heading("The company expects:", colon=True)
    assert is_dateline("COLUMBUS, Ohio Oct. 28, 2026 – Vertiv Holdings Co (NYSE: VRT) reported")
    assert is_dateline("MANCHESTER, Conn. - Bob's Discount Furniture, Inc. today announced")
    assert is_dateline("IRVINE, Calif. — (BUSINESS WIRE) — October 29, 2026 — Ingram Micro")
    assert not is_dateline("Net sales are expected to be $1.2 billion.")
    assert dates_only("July 26, April 26, July 27,") and dates_only("2026 2025 2026 2025")
    assert not dates_only("24,285 24,391 24,532")


def test_outlook_block_rules():
    text = "\n".join([
        "Third Quarter Results",
        "Revenue was $10 million.",
        "2027 Outlook:",
        "The Company expects the following for fiscal 2027:",
        "•Revenue of $40 to $42 million",
        "Low High",
        "Gross margin(1)",
        "40% - 42%",
        "We estimate the extra week adds about $1 million of revenue and is included in the "
        "ranges above, which assume no change in tariffs or exchange rates.",
        "Conference Call",
        "Management will host a call at 5 p.m. Eastern Time to discuss the results and the outlook "
        "for the year, with a replay available afterwards.",
    ])
    [block] = read_release(text).outlook
    assert block.heading == "2027 Outlook:"
    assert block.text.split("\n")[-1].startswith("We estimate the extra week")
    assert "Conference Call" not in block.text


def test_a_heading_with_nothing_under_it_is_kept_only_when_it_states_something():
    stated = "Raises Full-Year 2026 Revenue Guidance\nSAN JOSE, Calif. - Acme today reported."
    assert headings(read_release(stated).outlook) == ["Raises Full-Year 2026 Revenue Guidance"]
    bare = "2026 Outlook\nSAN JOSE, Calif. - Acme today reported results for the quarter."
    assert read_release(bare).outlook == []


def test_a_reconciliation_ends_at_another_tables_title():
    text = "\n".join([
        "Reconciliation of Net Income to Adjusted EBITDA",
        "Three Months Ended",
        "2026 2025",
        "Net income $ 10 $ 8",
        "Depreciation 2 2",
        "Adjusted EBITDA $ 12 $ 10",
        "Segment Results",
        "Three Months Ended",
        "Widgets $ 5 $ 4",
    ])
    [table] = read_release(text).adjustments
    assert table.text.split("\n")[-1] == "Adjusted EBITDA $ 12 $ 10"
