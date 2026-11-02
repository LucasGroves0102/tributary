import gzip
from pathlib import Path

import pytest

from filing_parser.sections import find_sections, html_to_text, parse

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


def test_no_titles_keeps_previous_result():
    assert find_sections("PART I\nNothing numbered or titled here.", "10-Q") == []
    with pytest.raises(ValueError):
        find_sections("No headings at all.", "10-Q")
