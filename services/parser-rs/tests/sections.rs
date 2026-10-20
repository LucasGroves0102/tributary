//! The Python parser's tests (services/parser/tests/test_sections.py), against the Rust parser.

use std::io::Read;
use std::path::Path;

use filing_parser::{Section, exhibit_section, find_sections, html_to_text, parse};

fn fixture(form: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../parser/tests/fixtures/nvda-{form}.htm.gz"));
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut out)
        .unwrap();
    out
}

fn keys(sections: &[Section]) -> Vec<&str> {
    sections.iter().map(|s| s.key.as_str()).collect()
}

fn section<'a>(sections: &'a [Section], key: &str) -> &'a Section {
    sections.iter().find(|s| s.key == key).unwrap()
}

fn chars(text: &str, start: usize, end: usize) -> String {
    text.chars().skip(start).take(end - start).collect()
}

#[test]
fn ten_k() {
    let raw = fixture("10-K");
    let sections = parse(&raw, "10-K").unwrap();
    assert_eq!(
        keys(&sections),
        [
            "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
            "10", "11", "12", "13", "14", "15", "16"
        ]
    );

    // the body, not the table of contents
    let risk = section(&sections, "1A");
    assert_eq!(risk.title, "Risk Factors");
    assert!(risk.text.chars().count() > 100_000);
    assert_eq!(risk.part.as_deref(), Some("I"));
    let mdna = section(&sections, "7");
    assert_eq!(mdna.part.as_deref(), Some("II"));
    let head: String = mdna.text.chars().take(500).collect();
    assert!(head.to_lowercase().contains("results of operations"));

    // offsets point into the extracted text
    let text = html_to_text(&raw).unwrap();
    for s in &sections {
        assert_eq!(chars(&text, s.char_start, s.char_end).trim(), s.text);
    }
}

#[test]
fn ten_q_keys_include_part() {
    let sections = parse(&fixture("10-Q"), "10-Q").unwrap();
    assert_eq!(
        keys(&sections),
        [
            "I-1", "I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-5", "II-6"
        ]
    );
}

#[test]
fn eight_k_decimal_items() {
    let sections = parse(&fixture("8-K"), "8-K").unwrap();
    assert_eq!(keys(&sections), ["8.01", "9.01"]);
    assert_eq!(sections[0].title, "Other Events.");
}

#[test]
fn hidden_inline_xbrl_header_is_dropped() {
    let raw = b"<html><body><div style='display: none'><ix:header>Item 1. Hidden</ix:header></div>\
                <p>Visible</p></body></html>";
    assert_eq!(html_to_text(raw).unwrap(), "Visible");
}

#[test]
fn cross_reference_does_not_steal_section() {
    let text = [
        "Item 1. Business",
        "Item 7. MD&A", // table of contents
        "Item 1. Business",
        &"We make chips. ".repeat(20),
        "Item 7 of this report discusses results.", // line-leading cross-reference
        &"More business text. ".repeat(5),
        "Item 7. MD&A",
        &"Revenue grew. ".repeat(50),
    ]
    .join("\n");
    let sections = find_sections(&text, "10-K").unwrap();
    assert!(
        section(&sections, "7")
            .text
            .starts_with("Item 7. MD&A\nRevenue grew.")
    );
}

#[test]
fn title_on_next_line() {
    let text = format!("Item 1A.\nRisk Factors\n{}", "Risky. ".repeat(10));
    assert_eq!(
        find_sections(&text, "10-K").unwrap()[0].title,
        "Risk Factors"
    );
}

#[test]
fn running_page_headers_are_ignored() {
    let page = "PART II\nItem 7\n";
    let text = [
        "PART I",
        "Item 1. Business",
        "PART II",
        "Item 7. MD&A", // table of contents
        "PART I",
        "Item 1. Business",
        &"We make chips. ".repeat(20),
        "PART II",
        "Item 7. MD&A",
        &"Page one. ".repeat(30),
        &(page.to_string() + &"Page two. ".repeat(30)),
        &(page.to_string() + &"Page three. ".repeat(30)),
    ]
    .join("\n");
    let sections = find_sections(&text, "10-K").unwrap();
    let mdna = section(&sections, "7");
    assert!(mdna.text.contains("Page one.") && mdna.text.contains("Page three."));
}

#[test]
fn ten_q_body_without_part_one_heading() {
    let text = [
        "Part I",
        "Item 1.",
        "Item 2.",
        "Part II",
        "Item 1A.",
        "Item 2.", // table of contents
        "Item 2. Management's Discussion",
        &"Revenue grew. ".repeat(30), // body: no "Part I"
        "PART II",
        "Item 1A. Risk Factors",
        &"Risky. ".repeat(30),
    ]
    .join("\n");
    assert_eq!(
        keys(&find_sections(&text, "10-Q").unwrap()),
        ["I-2", "II-1A"]
    );
}

fn mdna_10k(body_lines: &[&str]) -> String {
    let mut lines = vec![
        "Part II",
        "Item 7. Management’s Discussion and Analysis of Financial Condition",
        "Item 8. Financial Statements",
        "Part II",
        "Item 7. Management’s Discussion and Analysis of Financial Condition",
        "Reference is made to Management’s discussion and analysis in the Financial Section.",
        "Item 8. Financial Statements",
        "See the Financial Section.",
        "FINANCIAL SECTION",
        "Management’s discussion and analysis",
    ];
    lines.extend(body_lines);
    lines.join("\n")
}

#[test]
fn mdna_incorporated_by_reference_is_found() {
    let text = mdna_10k(&[
        "Management’s report on internal control over financial reporting", // contents
        "Management’s discussion and analysis",
        "Revenue grew on strong demand.",
        "Management’s discussion and analysis",
        "Liquidity remained strong.",
        "Report of Independent Registered Public Accounting Firm",
        "We have audited ...",
    ]);
    let sections = find_sections(&text, "10-K").unwrap();
    let mdna = section(&sections, "7");
    assert!(
        mdna.text
            .starts_with("Management’s discussion and analysis\nRevenue grew")
    );
    assert!(mdna.text.ends_with("Liquidity remained strong."));
    assert_eq!(
        chars(&text, mdna.char_start, mdna.char_end).trim(),
        mdna.text
    );
    assert!(mdna.title.starts_with("Management’s Discussion"));
}

#[test]
fn mdna_needs_a_run_of_headings() {
    let text = mdna_10k(&[
        "Revenue grew.",
        "Report of Independent Registered Public Accounting Firm",
        "We audited.",
    ]);
    let sections = find_sections(&text, "10-K").unwrap();
    // one MD&A line is not enough: Item 7 unchanged
    assert!(section(&sections, "7").text.starts_with("Item 7."));
}

#[test]
fn exhibit() {
    let raw =
        b"<html><body><p>NVIDIA Announces Results</p><p>Revenue of $46.7 billion</p></body></html>";
    let s = exhibit_section("EX-99.1", None, raw).unwrap();
    assert_eq!(
        (s.key.as_str(), s.item.as_str(), s.title.as_str(), s.part),
        ("EX-99.1", "99.1", "Exhibit 99.1", None)
    );
    assert_eq!(s.text, "NVIDIA Announces Results\nRevenue of $46.7 billion");
    assert_eq!((s.char_start, s.char_end), (0, s.text.len()));
}

#[test]
fn edgar_envelope_and_title_are_dropped() {
    let raw = b"<DOCUMENT>\n<TYPE>EX-99.1\n<SEQUENCE>2\n<FILENAME>pr.htm\n<DESCRIPTION>EX-99.1\n\
                <TEXT>\n<HTML><HEAD><TITLE>pr</TITLE></HEAD><BODY><P>Results</P></BODY></HTML>\n\
                </TEXT>\n</DOCUMENT>";
    assert_eq!(html_to_text(raw).unwrap(), "Results");
}
