import gzip
from pathlib import Path

import pytest

from filing_parser.sections import _tree, _walk, exhibit_section, find_sections, html_to_text, parse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(form: str) -> bytes:
    return gzip.decompress((FIXTURES / f"nvda-{form}.htm.gz").read_bytes())


@pytest.fixture(scope="module")
def ten_k():
    return parse(fixture("10-K"), "10-K")


def test_10k_items_in_order(ten_k):
    keys = [s.key for s in ten_k]
    assert keys == ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A",
                    "9B", "9C", "10", "11", "12", "13", "14", "15", "16"]


def test_10k_body_not_table_of_contents(ten_k):
    risk = next(s for s in ten_k if s.key == "1A")
    assert risk.title == "Risk Factors"
    assert len(risk.text) > 100_000
    assert risk.part == "I"
    mdna = next(s for s in ten_k if s.key == "7")
    assert mdna.part == "II"
    assert "results of operations" in mdna.text[:500].lower()


def test_offsets_point_into_extracted_text(ten_k):
    text = html_to_text(fixture("10-K"))
    for s in ten_k:
        assert text[s.char_start:s.char_end].strip() == s.text


def test_10q_keys_include_part():
    keys = [s.key for s in parse(fixture("10-Q"), "10-Q")]
    assert keys == ["I-1", "I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-5", "II-6"]


def test_a_decimal_item_run_into_its_title_keeps_its_number():
    """Rule 2: "Item 7.01Regulation FD Disclosure" (no space) was keyed 7 in 589 production rows; a
    decimal item ends before anything but a digit. "Item 7.012" stays item 7, as before."""
    body = "Text. " * 20
    text = "\n".join(["Item 2.02Results of Operations and Financial Condition", body,
                      "Item 7.01Regulation FD Disclosure", body,
                      "ITEM 9.01FINANCIAL STATEMENTS AND EXHIBITS", body])
    sections = find_sections(text, "8-K")
    assert [(s.key, s.item, s.title) for s in sections] == [
        ("2.02", "2.02", "Results of Operations and Financial Condition"),
        ("7.01", "7.01", "Regulation FD Disclosure"),
        ("9.01", "9.01", "FINANCIAL STATEMENTS AND EXHIBITS"),
    ]
    assert [s.key for s in find_sections("\n".join(["Item 7.012 Other", body]), "8-K")] == ["7"]


def test_8k_decimal_items():
    sections = parse(fixture("8-K"), "8-K")
    assert [s.key for s in sections] == ["8.01", "9.01"]
    assert sections[0].title == "Other Events."


def test_hidden_inline_xbrl_header_is_dropped():
    raw = (b"<html><body><div style='display: none'><ix:header>Item 1. Hidden</ix:header></div>"
           b"<p>Visible</p></body></html>")
    assert html_to_text(raw) == "Visible"


def test_cross_reference_does_not_steal_section():
    text = "\n".join([
        "Item 1. Business", "Item 7. MD&A",  # table of contents
        "Item 1. Business", "We make chips. " * 20,
        "Item 7 of this report discusses results.",  # line-leading cross-reference
        "More business text. " * 5,
        "Item 7. MD&A", "Revenue grew. " * 50,
    ])
    mdna = next(s for s in find_sections(text, "10-K") if s.key == "7")
    assert mdna.text.startswith("Item 7. MD&A\nRevenue grew.")


def test_title_on_next_line():
    text = "Item 1A.\nRisk Factors\n" + "Risky. " * 10
    assert find_sections(text, "10-K")[0].title == "Risk Factors"


def test_running_page_headers_are_ignored():
    page = "PART II\nItem 7\n"
    text = "\n".join([
        "PART I", "Item 1. Business", "PART II", "Item 7. MD&A",          # table of contents
        "PART I", "Item 1. Business", "We make chips. " * 20,
        "PART II", "Item 7. MD&A", "Page one. " * 30,
        page + "Page two. " * 30,
        page + "Page three. " * 30,
    ])
    mdna = next(s for s in find_sections(text, "10-K") if s.key == "7")
    assert "Page one." in mdna.text and "Page three." in mdna.text


def test_10q_body_without_part_one_heading():
    text = "\n".join([
        "Part I", "Item 1.", "Item 2.", "Part II", "Item 1A.", "Item 2.",  # table of contents
        "Item 2. Management's Discussion", "Revenue grew. " * 30,           # body: no "Part I"
        "PART II", "Item 1A. Risk Factors", "Risky. " * 30,
    ])
    keys = [s.key for s in find_sections(text, "10-Q")]
    assert keys == ["I-2", "II-1A"]


def _mdna_10k(*body_lines: str) -> str:
    head = ["Part II", "Item 7. Management’s Discussion and Analysis of Financial Condition",
            "Item 8. Financial Statements"]
    body = ["Part II",
            "Item 7. Management’s Discussion and Analysis of Financial Condition",
            "Reference is made to Management’s discussion and analysis in the Financial Section.",
            "Item 8. Financial Statements", "See the Financial Section.",
            "FINANCIAL SECTION", "Management’s discussion and analysis", *body_lines]
    return "\n".join(head + body)


def test_mdna_incorporated_by_reference_is_found():
    text = _mdna_10k(
        "Management’s report on internal control over financial reporting",  # contents
        "Management’s discussion and analysis", "Revenue grew on strong demand.",
        "Management’s discussion and analysis", "Liquidity remained strong.",
        "Report of Independent Registered Public Accounting Firm", "We have audited ...")
    mdna = next(s for s in find_sections(text, "10-K") if s.key == "7")
    assert mdna.text.startswith("Management’s discussion and analysis\nRevenue grew")
    assert mdna.text.endswith("Liquidity remained strong.")
    assert text[mdna.char_start:mdna.char_end].strip() == mdna.text
    assert mdna.title.startswith("Management’s Discussion")


def test_mdna_needs_a_run_of_headings():
    text = _mdna_10k("Revenue grew.",
                     "Report of Independent Registered Public Accounting Firm", "We audited.")
    mdna = next(s for s in find_sections(text, "10-K") if s.key == "7")
    assert mdna.text.startswith("Item 7.")  # one MD&A line is not enough: Item 7 unchanged


def test_exhibit_section():
    from filing_parser.sections import exhibit_section

    s = exhibit_section("EX-99.1", None, b"<html><body><p>NVIDIA Announces Results</p>"
                                         b"<p>Revenue of $46.7 billion</p></body></html>")
    assert (s.key, s.item, s.title, s.part) == ("EX-99.1", "99.1", "Exhibit 99.1", None)
    assert s.text == "NVIDIA Announces Results\nRevenue of $46.7 billion"
    assert (s.char_start, s.char_end) == (0, len(s.text))


def test_edgar_envelope_and_title_are_dropped():
    raw = (b"<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>pr.htm\n<DESCRIPTION>EX-99.1\n"
           b"<TEXT>\n<HTML><HEAD><TITLE>pr</TITLE></HEAD><BODY><P>Results</P></BODY></HTML>\n"
           b"</TEXT>\n</DOCUMENT>")
    assert html_to_text(raw) == "Results"


# rule 9: filings whose items are titled but not numbered (real Q3 2026 10-Qs)

def _titled(name: str) -> tuple[str, list]:
    raw = gzip.decompress((FIXTURES / f"{name}-10-Q.htm.gz").read_bytes())
    return html_to_text(raw), parse(raw, "10-Q")


def _check_offsets(text: str, sections: list) -> None:
    for s in sections:
        assert text[s.char_start:s.char_end].strip() == s.text


def test_titles_with_part_headings():
    # Molina: "PART I—FINANCIAL INFORMATION", then "RISK FACTORS"; no Item 1 or 6 title
    text, sections = _titled("moh")
    assert [s.key for s in sections] == ["I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-5"]
    mdna = next(s for s in sections if s.key == "I-2")
    assert mdna.title.startswith("MANAGEMENT’S DISCUSSION AND ANALYSIS")  # (“MD&A”) suffix
    assert mdna.part == "I" and len(mdna.text) > 50_000
    risk = next(s for s in sections if s.key == "II-1A")
    assert risk.text.startswith("RISK FACTORS\nCertain risks may have a material adverse")
    # the "Legal Proceedings" note in Part I's financial statements is not Part II, Item 1
    legal = next(s for s in sections if s.key == "II-1")
    assert legal.text.startswith("LEGAL PROCEEDINGS\nFor information regarding legal")
    _check_offsets(text, sections)


def test_titles_without_part_headings():
    # Edison: titles only, in the body and in a table of contents with "Part I, Item 2" lines
    text, sections = _titled("eix")
    assert [s.key for s in sections] == ["I-2", "I-3", "I-1", "I-4", "II-1", "II-2", "II-5",
                                         "II-6"]
    mdna = next(s for s in sections if s.key == "I-2")
    assert mdna.text.startswith("MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND"
                                " RESULTS OF OPERATIONS\nMANAGEMENT OVERVIEW\nHighlights")
    assert len(mdna.text) > 50_000
    _check_offsets(text, sections)


def test_titles_with_cross_reference_index():
    # TriNet: index lines "Part I, Item 2. 7" are not Part headings; running page headers
    text, sections = _titled("tnet")
    assert [s.key for s in sections] == ["I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-3",
                                         "II-4", "II-5", "II-6"]
    mdna = next(s for s in sections if s.key == "I-2")
    assert mdna.text.startswith("Management’s Discussion and Analysis of Financial Condition and"
                                " Results of Operations\nExecutive Summary")
    risk = next(s for s in sections if s.key == "II-1A")
    assert risk.text.startswith("Risk Factors\nThere have been no material changes")
    _check_offsets(text, sections)


def test_titles_ignored_when_items_are_numbered():
    text = "\n".join(["Item 1A. Risk Factors", "Risky. " * 30,
                      "LEGAL PROCEEDINGS", "None. " * 30])
    assert [s.key for s in find_sections(text, "10-K")] == ["1A"]
    with pytest.raises(ValueError):  # rule 9 is for 10-K and 10-Q only
        find_sections(text.replace("Item 1A. ", ""), "8-K")


def test_title_core_and_part():
    text = "\n".join([
        "Management’s Discussion and Analysis of Financial Condition and Results of Operations",
        "Part II, Item 2. 7",  # contents; a cross-reference, not a Part heading
        "PART I",
        "Management's Discussion and Analysis of Results of Operations and Financial Condition"
        " (MD&A):",
        "Revenue grew. " * 30,
        "Legal proceedings",  # Part II title inside Part I: skipped
        "More revenue. " * 30,
        "PART II",
        "Risk Factors.", "Risky. " * 30,
        "RISK FACTORS",  # repeat of the current section: running header
        "Still risky. " * 30,
        "Exhibits (a list of more than twenty characters)",  # not a title
    ])
    sections = find_sections(text, "10-Q")
    assert [(s.key, s.part) for s in sections] == [("I-2", "I"), ("II-1A", "II")]
    assert "More revenue." in sections[0].text
    assert "Still risky." in sections[1].text
    assert sections[0].title.endswith("(MD&A):")


def test_titles_10k():
    text = "\n".join(["BUSINESS", "We make chips. " * 30, "ITEM-LESS RISK FACTORS",
                      "Risk factors", "Risky. " * 30, "Form 10-K Summary", "None."])
    sections = find_sections(text, "10-K")
    assert [(s.key, s.part) for s in sections] == [("1", "I"), ("1A", "I"), ("16", "IV")]


def _titles(text: str, form: str) -> dict[str, str]:
    return {s.key: s.title for s in find_sections(text, form)}


def test_rule_10_leading_punctuation_and_quotes():
    """M4 (audit 2026-11-06): titles from production that started with punctuation."""
    body = "Text. " * 20
    text = "\n".join([
        "Item 2.02, \"Results of Operations and Financial Condition\"", body,
        "Item 7.01 ‑  Regulation FD Disclosure", body,
        "Item 8.01‎ Other Events.", body,
        "Item 9.01 , “Financial Statements and Exhibits.” and more", body,
    ])
    assert _titles(text, "8-K") == {
        "2.02": "Results of Operations and Financial Condition",
        "7.01": "Regulation FD Disclosure",
        "8.01": "Other Events.",
        "9.01": "Financial Statements and Exhibits.",
    }
    text = "\n".join(["Item 1. | Business", body, "Item 1A. “Risk", body,
                      "Item 6. [Reserved]", body, "Item 9A. …..CONTROLS AND PROCEDURES…..", body,
                      "Item 9B. ,", body])
    assert _titles(text, "10-K") == {"1": "Business", "1A": "Risk", "6": "[Reserved]",
                                     "9A": "CONTROLS AND PROCEDURES", "9B": ""}


def test_rule_10_cross_reference_title_is_the_quoted_one():
    """Deckers' 10-K: the Item 7 heading kept is a cross-reference into a quoted title."""
    text = "\n".join([
        "Item 7, “Management’s Discussion and Analysis of Financial Condition and Results of"
        " Operations,” of our Annual", "Report. " * 30])
    assert _titles(text, "10-K")["7"] == ("Management’s Discussion and Analysis of Financial"
                                          " Condition and Results of Operations")


def test_rule_10_wrapped_titles_are_completed():
    text = "\n".join([
        "Item 7. MANAGEMENT’S", "DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND",
        "RESULTS OF OPERATIONS", "Revenue grew. " * 30,
        "Item 7A. Quantitative and Qualitative Disclosures", "about Market Risk.", "Rates. " * 30,
        # the filer's own short title: the next line doesn't continue it, so it stays
        "Item 9. Changes in and Disagreements", "None.",
        "Item 9A. Controls", "Our controls work. " * 10,
    ])
    titles = _titles(text, "10-K")
    assert titles["7"] == ("MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND"
                           " RESULTS OF OPERATIONS")
    assert titles["7A"] == "Quantitative and Qualitative Disclosures about Market Risk."
    assert titles["9"] == "Changes in and Disagreements"
    assert titles["9A"] == "Controls"
    # a 10-Q's title by its Part: Part I item 2 is MD&A, never Part II item 2's title
    text = "\n".join(["PART I", "Item 2. Management’s Discussion and Analysis",
                      "of Financial Condition and Results of Operations", "Sales. " * 30,
                      "PART II", "Item 2. Unregistered Sales of Equity Securities",
                      "and Use of Proceeds", "None. " * 5])
    assert _titles(text, "10-Q") == {
        "I-2": ("Management’s Discussion and Analysis of Financial Condition and Results"
                " of Operations"),
        "II-2": "Unregistered Sales of Equity Securities and Use of Proceeds",
    }


def test_rule_10_wrapping_stops_after_three_lines_and_at_long_lines():
    text = "\n".join(["Item 7. Management's", "Discussion", "and", "Analysis",
                      "of Financial Condition", "Body. " * 30])
    assert _titles(text, "10-K")["7"] == "Management's Discussion and Analysis"
    text = "\n".join(["Item 7. Management's Discussion and Analysis of", "Financial " + "x" * 120,
                      "Body. " * 30])
    assert _titles(text, "10-K")["7"] == "Management's Discussion and Analysis of"


def test_rule_10_changes_titles_only(ten_k):
    """Same keys, text and offsets as rules 1-9 give; the NVIDIA fixtures' titles were clean."""
    assert [s.title for s in ten_k][:3] == ["Business", "Risk Factors", "Unresolved Staff Comments"]


def test_no_titles_keeps_previous_result():
    assert find_sections("PART I\nNothing numbered or titled here.", "10-Q") == []
    with pytest.raises(ValueError):
        find_sections("No headings at all.", "10-Q")


# --- rule 11: tables beside the text ------------------------------------------------------------

def _rows(section) -> list[list[str]]:
    return [[c.text for c in r.cells] for t in section.tables for r in t.rows]


def test_tables_rows_cells_spans_and_offsets():
    raw = (b"<html><body><p>Item 2.02 Results</p><p>Intro.</p>"
           b"<table><thead><tr><td></td><td colspan='3'>Three Months Ended</td></tr></thead>"
           b"<tr><td></td><td>2026</td><td></td><td>2025</td></tr>"
           b"<tr><td><p>Net   sales</p></td><td>$</td><td>1,234</td><td>$&nbsp; 1,100 </td></tr>"
           b"<tr><td> </td><td></td></tr>"  # a spacer row: dropped
           b"<tr><th>Margin</th><th>45.1</th><td> % </td></tr>"
           b"</table><p>After.</p></body></html>")
    (s,) = parse(raw, "8-K")
    assert s.text == ("Item 2.02 Results\nIntro.\nThree Months Ended\n2026 2025\nNet sales\n$ 1,234"
                      " $ 1,100\nMargin 45.1 %\nAfter.")
    (t,) = s.tables
    assert [[c.text for c in r.cells] for r in t.rows] == [
        ["", "Three Months Ended"], ["", "2026", "", "2025"],
        ["Net sales", "$", "1,234", "$ 1,100"], ["Margin", "45.1", "%"]]
    assert [c.span for c in t.rows[0].cells] == [1, 3]
    assert [r.head for r in t.rows] == [True, False, False, False]  # thead; th and a td: not all th
    assert [s.text[r.start:r.end] for r in t.rows] == [
        "Three Months Ended", "2026 2025", "Net sales\n$ 1,234 $ 1,100", "Margin 45.1 %"]
    assert (t.start, t.end) == (t.rows[0].start, t.rows[-1].end)


def test_tables_head_rows_of_th_cells_nested_tables_and_cells_outside_rows():
    raw = (b"<div>Item 8.01 Other</div><table>"
           b"<tr><th>Metric</th><th></th><th>Low</th></tr>"
           b"<tr><td>Revenue<table><tr><td>inner</td><td>x</td></tr><tr><td>y</td></tr></table></td>"
           b"<td>2.5</td></tr><td>loose cell</td></table>")
    (s,) = parse(raw, "8-K")
    outer, inner = s.tables
    assert [r.head for r in outer.rows] == [True, False]
    assert [[c.text for c in r.cells] for r in outer.rows] == [
        ["Metric", "", "Low"], ["Revenueinner x y", "2.5"]]  # a table starts no line
    assert [[c.text for c in r.cells] for r in inner.rows] == [["inner", "x"], ["y"]]


def test_tables_colspan_values():
    def spans(value: bytes) -> int:
        raw = (b"<p>Item 8.01 Other</p><table><tr><td colspan" + value + b">a</td></tr>"
               b"<tr><td>b</td></tr></table>")
        return parse(raw, "8-K")[0].tables[0].rows[0].cells[0].span
    assert [spans(v) for v in (b"=2", b"='\n 3'", b"=4px", b"=0", b"=x", b"", b"=999999999999",
                                b"=&#53;", b"='+2'")] == [2, 3, 4, 1, 1, 1, 100, 5, 1]


def test_tables_keep_only_rows_inside_the_section():
    """A table straddling an Item heading gives each section the rows within it; a section keeps
    a table only with two rows or more."""
    raw = (b"<p>Item 1.01 Agreement</p><table><tr><td>a</td><td>1</td></tr>"
           b"<tr><td>b</td><td>2</td></tr><tr><td>Item 2.03 Obligation</td></tr>"
           b"<tr><td>c</td><td>3</td></tr></table><p>Text.</p>")
    first, second = parse(raw, "8-K")
    assert _rows(first) == [["a", "1"], ["b", "2"]]
    assert second.key == "2.03" and _rows(second) == [["Item 2.03 Obligation"], ["c", "3"]]
    one_row = b"<p>Item 1.01 Agreement</p><table><tr><td>a</td><td>1</td></tr></table><p>x</p>"
    assert parse(one_row, "8-K")[0].tables == []


def test_tables_of_an_exhibit_and_hidden_ones():
    raw = (b"<html><body><p>Outlook</p><table><tr><td>Revenue</td><td>$2.5 billion</td></tr>"
           b"<tr><td>Margin</td><td>20%</td></tr></table>"
           b"<table style='display:none'><tr><td>h</td></tr><tr><td>i</td></tr></table>"
           b"</body></html>")
    s = exhibit_section("EX-99.1", "Press release", raw)
    assert _rows(s) == [["Revenue", "$2.5 billion"], ["Margin", "20%"]]
    assert s.tables[0].start == len("Outlook\n")


@pytest.mark.parametrize("name,form,least", [("nvda-10-K", "10-K", 600), ("nvda-10-Q", "10-Q", 300),
                                             ("nvda-8-K", "8-K", 6), ("moh-10-Q", "10-Q", 80)])
def test_tables_on_real_filings(name, form, least):
    """Each row's text is its non-empty cells joined by a space (a "\\n" read as one), the
    tables lie in their section in order, and the walk rebuilds text_content() exactly."""
    raw = gzip.decompress((FIXTURES / f"{name}.htm.gz").read_bytes())
    doc = _tree(raw)
    assert _walk(doc)[0] == doc.text_content()
    sections = parse(raw, form)
    assert [(s.key, s.text, s.char_start, s.char_end) for s in sections] == [
        (s.key, s.text, s.char_start, s.char_end) for s in find_sections(html_to_text(raw), form)]
    rows = 0
    for s in sections:
        last = -1
        for t in s.tables:
            assert last <= t.start < t.end <= len(s.text) and len(t.rows) >= 2
            for r in t.rows:
                rows += 1
                assert s.text[r.start:r.end].replace("\n", " ") == " ".join(
                    c.text for c in r.cells if c.text)
            last = t.start
    assert rows >= least


# rules 12 and 13: Form 20-F items, 6-K and 40-F documents. A 20-F laid out as most are (Workiva and
# Donnelley: a contents table listing items and sub-items, "3.D. Risk Factors" under Item 3, Item 18
# pointing to the F-pages). No 20-F, 40-F or 6-K was stored on hermes when these rules were written:
# the documents below are shaped like them, not copied.

TWENTY_F = (
    b"<html><body><p>FORM 20-F</p><p>TABLE OF CONTENTS</p><table>"
    b"<tr><td>PART I</td></tr>"
    b"<tr><td>Item 1.</td><td>Identity of Directors, Senior Management and Advisers</td></tr>"
    b"<tr><td>Item 3.</td><td>Key Information</td></tr>"
    b"<tr><td>Item 3.D.</td><td>Risk Factors</td></tr>"
    b"<tr><td>Item 4.</td><td>Information on the Company</td></tr>"
    b"<tr><td>Item 4A.</td><td>Unresolved Staff Comments</td></tr>"
    b"<tr><td>Item 5.</td><td>Operating and Financial Review and Prospects</td></tr>"
    b"<tr><td>PART III</td></tr>"
    b"<tr><td>Item 18.</td><td>Financial Statements</td></tr>"
    b"<tr><td>Item 19.</td><td>Exhibits</td></tr></table>"
    b"<p>PART I</p>"
    b"<p>ITEM 1. IDENTITY OF DIRECTORS, SENIOR MANAGEMENT AND ADVISERS</p><p>Not applicable.</p>"
    b"<p>ITEM 3. KEY INFORMATION</p><p>3.A. [Reserved]</p><p>3.B. Capitalization and"
    b" Indebtedness</p><p>Not applicable.</p>"
    b"<p>3.D. RISK FACTORS</p><p>Risks Relating to Our Business</p>"
    b"<p>Demand for our wafers may fall.</p>"
    b"<p>ITEM 4. INFORMATION ON THE COMPANY</p><p>4.A. History and Development</p>"
    b"<p>We were founded in 1987.</p><p>Item 4.B. Business Overview</p><p>We make chips.</p>"
    b"<p>ITEM 4A. UNRESOLVED STAFF COMMENTS</p><p>None.</p>"
    b"<p>ITEM 5. OPERATING AND FINANCIAL REVIEW AND PROSPECTS</p>"
    b"<p>Item 5.A. Operating Results</p><p>Net revenue rose 31.6%.</p>"
    b"<p>Item 5.B. Liquidity and Capital Resources</p><p>Cash was NT$2.1 trillion.</p>"
    b"<p>PART III</p>"
    b"<p>ITEM 18. FINANCIAL STATEMENTS</p><p>See pages F-1 through F-90.</p>"
    b"<p>ITEM 19. EXHIBITS</p><p>1.1 Articles of Incorporation</p>"
    b"</body></html>"
)


def test_20f_items_with_3d_and_sub_items_kept_in_their_item():
    text = html_to_text(TWENTY_F)
    sections = parse(TWENTY_F, "20-F")
    assert [s.key for s in sections] == ["1", "3", "3.D", "4", "4A", "5", "18", "19"]
    by = {s.key: s for s in sections}
    assert by["3"].text == ("ITEM 3. KEY INFORMATION\n3.A. [Reserved]\n3.B. Capitalization and"
                            " Indebtedness\nNot applicable.")
    risk = by["3.D"]
    assert (risk.item, risk.part, risk.title) == ("3.D", "I", "3.D. RISK FACTORS")
    assert risk.text.endswith("Demand for our wafers may fall.")
    assert "Business Overview\nWe make chips." in by["4"].text  # a sub-item stays in its item
    assert by["5"].text.endswith("Cash was NT$2.1 trillion.")  # 5.A and 5.B: one Item 5
    assert by["18"].part == "III" and by["4A"].title == "UNRESOLVED STAFF COMMENTS"
    _check_offsets(text, sections)
    assert by["3"].char_end == risk.char_start


def test_20f_sub_item_headings_and_3d_lines():
    from filing_parser.sections import _headings, _is_3d_heading

    subs = [(h.item, h.sub) for h in _headings(
        "Item 3.D Risk\nITEM 5.A.\nItem 3.Dividends\nItem 4A. x\nItem 2.02.A\nItem 4.b")]
    assert subs == [("3", True), ("5", True), ("3", False), ("4A", False), ("2.02", False),
                    ("4", True)]
    for line in ("D. Risk Factors", "3.D RISK FACTORS", "Item 3.D. Risk factors", "Risk Factors",
                 "D Risk Factors (continued)", "İtem 3.d - Rİsk factors", "3D. Risk Factors:"):
        assert _is_3d_heading(line), line
    for line in ("Summary of Risk Factors", "dRisk Factors", "E. Risk Factors",
                 "Item 3. Risk Factors", "D. Risk Factors" + " " * 120):
        assert not _is_3d_heading(line), line


def test_20f_lettered_sub_items_stay_in_their_item_and_16a_16k_are_items():
    text = ("PART I\nItem 5. Operating Review\nItem 5A. Operating Results\nRevenue.\nPART II\n"
            "Item 16A. Audit Committee\nx\nItem 16F. Change in Accountant\nNone.\nItem 16L. y\nz")
    sections = find_sections(text, "20-F")
    assert [s.key for s in sections] == ["5", "16A", "16F"]
    assert sections[0].text.endswith("Revenue.") and sections[2].text.endswith("Item 16L. y\nz")


def test_20f_without_3d_line_keeps_item_3_whole():
    sections = find_sections("PART I\nItem 3. Key Information\nNone.\nItem 4. The Company\nx",
                             "20-F")
    assert [s.key for s in sections] == ["3", "4"]


def test_20f_laid_out_by_a_cross_reference_index_is_one_section():
    text = "Annual Report and Form 20-F 2025\nStrategic report\nRisk factors\nOur risks."
    (s,) = find_sections(text, "20-F/A")
    assert (s.key, s.item, s.part, s.title) == ("20-F", "20-F", None, "Form 20-F")
    assert (s.text, s.char_start, s.char_end) == (text, 0, len(text))


def test_6k_and_40f_primary_documents_are_one_section_with_their_tables():
    raw = (b"<html><body><p>TSMC Reports Third Quarter EPS of NT$17.44</p><p>Item 2. x</p>"
           b"<table><tr><td>Net revenue</td><td>989.92</td></tr>"
           b"<tr><td>Gross margin</td><td>59.5%</td></tr></table></body></html>")
    for form, key in (("6-K", "6-K"), ("40-F", "40-F"), ("40-F/A", "40-F")):
        (s,) = parse(raw, form)
        assert (s.key, s.item, s.part, s.title) == (key, key, None, f"Form {key}")
        assert s.text == html_to_text(raw) and (s.char_start, s.char_end) == (0, len(s.text))
        assert [[c.text for c in r.cells] for r in s.tables[0].rows] == [
            ["Net revenue", "989.92"], ["Gross margin", "59.5%"]]
    with pytest.raises(ValueError):
        parse(b"<html><body><p> </p></body></html>", "6-K")
