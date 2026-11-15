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

/// Rule 2: "Item 7.01Regulation FD Disclosure" (no space) was keyed 7 in production; a decimal
/// item ends before anything but a digit. "Item 7.012" stays item 7. (sections.py's
/// test_a_decimal_item_run_into_its_title_keeps_its_number.)
#[test]
fn a_decimal_item_run_into_its_title_keeps_its_number() {
    let body = "Text. ".repeat(20);
    let text = [
        "Item 2.02Results of Operations and Financial Condition",
        &body,
        "Item 7.01Regulation FD Disclosure",
        &body,
        "ITEM 9.01FINANCIAL STATEMENTS AND EXHIBITS",
        &body,
    ]
    .join("\n");
    let s = find_sections(&text, "8-K").unwrap();
    let got: Vec<(&str, &str, &str)> = s
        .iter()
        .map(|x| (x.key.as_str(), x.item.as_str(), x.title.as_str()))
        .collect();
    assert_eq!(
        got,
        [
            (
                "2.02",
                "2.02",
                "Results of Operations and Financial Condition"
            ),
            ("7.01", "7.01", "Regulation FD Disclosure"),
            ("9.01", "9.01", "FINANCIAL STATEMENTS AND EXHIBITS"),
        ]
    );
    let text = ["Item 7.012 Other", &body].join("\n");
    assert_eq!(keys(&find_sections(&text, "8-K").unwrap()), ["7"]);
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

// rule 9: filings whose items are titled but not numbered (real Q3 2026 10-Qs)

fn titled(name: &str) -> (String, Vec<Section>) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../parser/tests/fixtures/{name}-10-Q.htm.gz"));
    let mut raw = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut raw)
        .unwrap();
    (html_to_text(&raw).unwrap(), parse(&raw, "10-Q").unwrap())
}

fn check_offsets(text: &str, sections: &[Section]) {
    for s in sections {
        assert_eq!(chars(text, s.char_start, s.char_end).trim(), s.text);
    }
}

#[test]
fn titles_with_part_headings() {
    // Molina: "PART I—FINANCIAL INFORMATION", then "RISK FACTORS"; no Item 1 or 6 title
    let (text, sections) = titled("moh");
    assert_eq!(
        keys(&sections),
        ["I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-5"]
    );
    let mdna = section(&sections, "I-2");
    assert!(
        mdna.title
            .starts_with("MANAGEMENT’S DISCUSSION AND ANALYSIS")
    );
    assert_eq!(mdna.part.as_deref(), Some("I"));
    assert!(mdna.text.chars().count() > 50_000);
    assert!(
        section(&sections, "II-1A")
            .text
            .starts_with("RISK FACTORS\nCertain risks may have a material adverse")
    );
    // the "Legal Proceedings" note in Part I's financial statements is not Part II, Item 1
    assert!(
        section(&sections, "II-1")
            .text
            .starts_with("LEGAL PROCEEDINGS\nFor information regarding legal")
    );
    check_offsets(&text, &sections);
}

#[test]
fn titles_without_part_headings() {
    // Edison: titles only, in the body and in a table of contents with "Part I, Item 2" lines
    let (text, sections) = titled("eix");
    assert_eq!(
        keys(&sections),
        ["I-2", "I-3", "I-1", "I-4", "II-1", "II-2", "II-5", "II-6"]
    );
    let mdna = section(&sections, "I-2");
    assert!(mdna.text.starts_with(
        "MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF \
         OPERATIONS\nMANAGEMENT OVERVIEW\nHighlights"
    ));
    assert!(mdna.text.chars().count() > 50_000);
    check_offsets(&text, &sections);
}

#[test]
fn titles_with_cross_reference_index() {
    // TriNet: index lines "Part I, Item 2. 7" are not Part headings; running page headers
    let (text, sections) = titled("tnet");
    assert_eq!(
        keys(&sections),
        [
            "I-2", "I-3", "I-4", "II-1", "II-1A", "II-2", "II-3", "II-4", "II-5", "II-6"
        ]
    );
    assert!(section(&sections, "I-2").text.starts_with(
        "Management’s Discussion and Analysis of Financial Condition and Results of \
         Operations\nExecutive Summary"
    ));
    assert!(
        section(&sections, "II-1A")
            .text
            .starts_with("Risk Factors\nThere have been no material changes")
    );
    check_offsets(&text, &sections);
}

#[test]
fn titles_ignored_when_items_are_numbered() {
    let text = [
        "Item 1A. Risk Factors",
        &"Risky. ".repeat(30),
        "LEGAL PROCEEDINGS",
        &"None. ".repeat(30),
    ]
    .join("\n");
    assert_eq!(keys(&find_sections(&text, "10-K").unwrap()), ["1A"]);
    // rule 9 is for 10-K and 10-Q only
    assert!(find_sections(&text.replace("Item 1A. ", ""), "8-K").is_err());
}

#[test]
fn title_core_and_part() {
    let text = [
        "Management’s Discussion and Analysis of Financial Condition and Results of Operations",
        "Part II, Item 2. 7", // contents; a cross-reference, not a Part heading
        "PART I",
        "Management's Discussion and Analysis of Results of Operations and Financial Condition \
         (MD&A):",
        &"Revenue grew. ".repeat(30),
        "Legal proceedings", // Part II title inside Part I: skipped
        &"More revenue. ".repeat(30),
        "PART II",
        "Risk Factors.",
        &"Risky. ".repeat(30),
        "RISK FACTORS", // repeat of the current section: running header
        &"Still risky. ".repeat(30),
        "Exhibits (a list of more than twenty characters)", // not a title
    ]
    .join("\n");
    let sections = find_sections(&text, "10-Q").unwrap();
    let got: Vec<(&str, Option<&str>)> = sections
        .iter()
        .map(|s| (s.key.as_str(), s.part.as_deref()))
        .collect();
    assert_eq!(got, [("I-2", Some("I")), ("II-1A", Some("II"))]);
    assert!(sections[0].text.contains("More revenue."));
    assert!(sections[1].text.contains("Still risky."));
    assert!(sections[0].title.ends_with("(MD&A):"));
}

#[test]
fn titles_10k() {
    let text = [
        "BUSINESS",
        &"We make chips. ".repeat(30),
        "ITEM-LESS RISK FACTORS",
        "Risk factors",
        &"Risky. ".repeat(30),
        "Form 10-K Summary",
        "None.",
    ]
    .join("\n");
    let sections = find_sections(&text, "10-K").unwrap();
    let got: Vec<(&str, Option<&str>)> = sections
        .iter()
        .map(|s| (s.key.as_str(), s.part.as_deref()))
        .collect();
    assert_eq!(
        got,
        [("1", Some("I")), ("1A", Some("I")), ("16", Some("IV"))]
    );
}

#[test]
fn no_titles_keeps_previous_result() {
    assert!(
        find_sections("PART I\nNothing numbered or titled here.", "10-Q")
            .unwrap()
            .is_empty()
    );
    assert!(find_sections("No headings at all.", "10-Q").is_err());
}

#[test]
fn rule_10_leading_punctuation_and_quotes() {
    let body = "Text. ".repeat(20);
    let text = [
        "Item 2.02, \"Results of Operations and Financial Condition\"",
        &body,
        "Item 7.01 \u{2011} \u{2002}Regulation FD Disclosure",
        &body,
        "Item 8.01\u{200e} Other Events.",
        &body,
        "Item 9.01 , \u{201c}Financial Statements and Exhibits.\u{201d} and more",
        &body,
    ]
    .join("\n");
    let s = find_sections(&text, "8-K").unwrap();
    assert_eq!(
        section(&s, "2.02").title,
        "Results of Operations and Financial Condition"
    );
    assert_eq!(section(&s, "7.01").title, "Regulation FD Disclosure");
    assert_eq!(section(&s, "8.01").title, "Other Events.");
    assert_eq!(
        section(&s, "9.01").title,
        "Financial Statements and Exhibits."
    );
    let text = [
        "Item 1. | Business",
        &body,
        "Item 1A. \u{201c}Risk",
        &body,
        "Item 6. [Reserved]",
        &body,
        "Item 9A. \u{2026}..CONTROLS AND PROCEDURES\u{2026}..",
        &body,
        "Item 9B. ,",
        &body,
    ]
    .join("\n");
    let s = find_sections(&text, "10-K").unwrap();
    assert_eq!(section(&s, "1").title, "Business");
    assert_eq!(section(&s, "1A").title, "Risk");
    assert_eq!(section(&s, "6").title, "[Reserved]");
    assert_eq!(section(&s, "9A").title, "CONTROLS AND PROCEDURES");
    assert_eq!(section(&s, "9B").title, "");
}

#[test]
fn rule_10_cross_reference_title_is_the_quoted_one() {
    let heading = "Item 7, \u{201c}Management\u{2019}s Discussion and Analysis of Financial \
                   Condition and Results of Operations,\u{201d} of our Annual";
    let text = format!("{heading}\n{}", "Report. ".repeat(30));
    let s = find_sections(&text, "10-K").unwrap();
    let mdna = "Management\u{2019}s Discussion and Analysis of Financial Condition and Results \
                of Operations";
    assert_eq!(section(&s, "7").title, mdna);
}

#[test]
fn rule_10_wrapped_titles_are_completed() {
    let text = [
        "Item 7. MANAGEMENT\u{2019}S",
        "DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND",
        "RESULTS OF OPERATIONS",
        &"Revenue grew. ".repeat(30),
        "Item 7A. Quantitative and Qualitative Disclosures",
        "about Market Risk.",
        &"Rates. ".repeat(30),
        // the filer's own short title: the next line doesn't continue it, so it stays
        "Item 9. Changes in and Disagreements",
        "None.",
        "Item 9A. Controls",
        &"Our controls work. ".repeat(10),
    ]
    .join("\n");
    let s = find_sections(&text, "10-K").unwrap();
    let mdna = "MANAGEMENT\u{2019}S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS \
                OF OPERATIONS";
    assert_eq!(section(&s, "7").title, mdna);
    let risk = "Quantitative and Qualitative Disclosures about Market Risk.";
    assert_eq!(section(&s, "7A").title, risk);
    assert_eq!(section(&s, "9").title, "Changes in and Disagreements");
    assert_eq!(section(&s, "9A").title, "Controls");
    // a 10-Q's title by its Part: Part I item 2 is MD&A, never Part II item 2's title
    let text = [
        "PART I",
        "Item 2. Management\u{2019}s Discussion and Analysis",
        "of Financial Condition and Results of Operations",
        &"Sales. ".repeat(30),
        "PART II",
        "Item 2. Unregistered Sales of Equity Securities",
        "and Use of Proceeds",
        &"None. ".repeat(5),
    ]
    .join("\n");
    let s = find_sections(&text, "10-Q").unwrap();
    let mdna = "Management\u{2019}s Discussion and Analysis of Financial Condition and Results \
                of Operations";
    assert_eq!(section(&s, "I-2").title, mdna);
    let sales = "Unregistered Sales of Equity Securities and Use of Proceeds";
    assert_eq!(section(&s, "II-2").title, sales);
}

#[test]
fn rule_10_wrapping_stops_after_three_lines_and_at_long_lines() {
    let text = [
        "Item 7. Management's",
        "Discussion",
        "and",
        "Analysis",
        "of Financial Condition",
        &"Body. ".repeat(30),
    ]
    .join("\n");
    let s = find_sections(&text, "10-K").unwrap();
    let three = "Management's Discussion and Analysis";
    assert_eq!(section(&s, "7").title, three);
    let text = [
        "Item 7. Management's Discussion and Analysis of",
        &format!("Financial {}", "x".repeat(120)),
        &"Body. ".repeat(30),
    ]
    .join("\n");
    let s = find_sections(&text, "10-K").unwrap();
    let cut = "Management's Discussion and Analysis of";
    assert_eq!(section(&s, "7").title, cut);
}

#[test]
fn rule_10_changes_titles_only() {
    let sections = parse(&fixture("10-K"), "10-K").unwrap();
    let titles: Vec<&str> = sections.iter().take(3).map(|s| s.title.as_str()).collect();
    assert_eq!(
        titles,
        ["Business", "Risk Factors", "Unresolved Staff Comments"]
    );
}

// --- rule 11: tables beside the text (sections.py's test_tables_*) ---------------------------

/// Each table's rows as "cell|cell" joined by " / ", tables joined by " // ".
fn grid(s: &Section) -> String {
    let tables: Vec<String> = s
        .tables
        .iter()
        .map(|t| {
            let rows: Vec<String> = t
                .rows
                .iter()
                .map(|r| {
                    let cells: Vec<&str> = r.cells.iter().map(|c| c.text.as_str()).collect();
                    cells.join("|")
                })
                .collect();
            rows.join(" / ")
        })
        .collect();
    tables.join(" // ")
}

#[test]
fn tables_rows_cells_spans_and_offsets() {
    let raw = b"<html><body><p>Item 2.02 Results</p><p>Intro.</p>\
                <table><thead><tr><td></td><td colspan='3'>Three Months Ended</td></tr></thead>\
                <tr><td></td><td>2026</td><td></td><td>2025</td></tr>\
                <tr><td><p>Net   sales</p></td><td>$</td><td>1,234</td><td>$&nbsp; 1,100 </td></tr>\
                <tr><td> </td><td></td></tr>\
                <tr><th>Margin</th><th>45.1</th><td> % </td></tr>\
                </table><p>After.</p></body></html>";
    let s = parse(raw, "8-K").unwrap();
    assert_eq!(s.len(), 1);
    let text = [
        "Item 2.02 Results",
        "Intro.",
        "Three Months Ended",
        "2026 2025",
        "Net sales",
        "$ 1,234 $ 1,100",
        "Margin 45.1 %",
        "After.",
    ]
    .join("\n");
    assert_eq!(s[0].text, text);
    let want = "|Three Months Ended / |2026||2025 / Net sales|$|1,234|$ 1,100 / Margin|45.1|%";
    assert_eq!(grid(&s[0]), want);
    let t = &s[0].tables[0];
    let spans: Vec<u32> = t.rows[0].cells.iter().map(|c| c.span).collect();
    assert_eq!(spans, [1, 3]);
    let heads: Vec<bool> = t.rows.iter().map(|r| r.head).collect();
    assert_eq!(heads, [true, false, false, false]);
    let lines: Vec<String> = t
        .rows
        .iter()
        .map(|r| chars(&s[0].text, r.start, r.end))
        .collect();
    assert_eq!(
        lines,
        [
            "Three Months Ended",
            "2026 2025",
            "Net sales\n$ 1,234 $ 1,100",
            "Margin 45.1 %",
        ]
    );
    assert_eq!((t.start, t.end), (t.rows[0].start, t.rows[3].end));
}

#[test]
fn tables_head_rows_of_th_cells_nested_tables_and_cells_outside_rows() {
    let raw = b"<div>Item 8.01 Other</div><table>\
                <tr><th>Metric</th><th></th><th>Low</th></tr>\
                <tr><td>Revenue<table><tr><td>inner</td><td>x</td></tr><tr><td>y</td></tr></table>\
                </td><td>2.5</td></tr><td>loose cell</td></table>";
    let s = parse(raw, "8-K").unwrap();
    let want = "Metric||Low / Revenueinner x y|2.5 // inner|x / y";
    assert_eq!(grid(&s[0]), want);
    let heads: Vec<bool> = s[0].tables[0].rows.iter().map(|r| r.head).collect();
    assert_eq!(heads, [true, false]);
}

#[test]
fn tables_colspan_values() {
    const A: &str = "<p>Item 8.01 Other</p><table><tr>";
    const B: &str = "<tr><td>b</td></tr></table>";
    let span = |v: &str| {
        let raw = format!("{A}<td colspan{v}>a</td></tr>{B}");
        let s = parse(raw.as_bytes(), "8-K").unwrap();
        s[0].tables[0].rows[0].cells[0].span
    };
    let values = [
        "=2",
        "='\n 3'",
        "=4px",
        "=0",
        "=x",
        "",
        "=999999999999",
        "=&#53;",
        "='+2'",
    ];
    let spans: Vec<u32> = values.iter().map(|&v| span(v)).collect();
    assert_eq!(spans, [2, 3, 4, 1, 1, 1, 100, 5, 1]);
}

#[test]
fn tables_keep_only_rows_inside_the_section() {
    let raw = b"<p>Item 1.01 Agreement</p><table><tr><td>a</td><td>1</td></tr>\
                <tr><td>b</td><td>2</td></tr><tr><td>Item 2.03 Obligation</td></tr>\
                <tr><td>c</td><td>3</td></tr></table><p>Text.</p>";
    let s = parse(raw, "8-K").unwrap();
    assert_eq!(grid(&s[0]), "a|1 / b|2");
    assert_eq!(s[1].key, "2.03");
    assert_eq!(grid(&s[1]), "Item 2.03 Obligation / c|3");
    let one_row = b"<p>Item 1.01 A</p><table><tr><td>a</td><td>1</td></tr></table><p>x</p>";
    assert!(parse(one_row, "8-K").unwrap()[0].tables.is_empty());
}

#[test]
fn tables_of_an_exhibit_and_hidden_ones() {
    let raw = b"<html><body><p>Outlook</p><table><tr><td>Revenue</td><td>$2.5 billion</td></tr>\
                <tr><td>Margin</td><td>20%</td></tr></table>\
                <table style='display:none'><tr><td>h</td></tr><tr><td>i</td></tr></table>\
                </body></html>";
    let s = exhibit_section("EX-99.1", Some("Release"), raw).unwrap();
    assert_eq!(grid(&s), "Revenue|$2.5 billion / Margin|20%");
    assert_eq!(s.tables[0].start, "Outlook\n".len());
}

#[test]
fn tables_on_real_filings() {
    for (name, form, least) in [("10-K", "10-K", 600), ("10-Q", "10-Q", 300), ("8-K", "8-K", 6)] {
        let raw = fixture(name);
        let sections = parse(&raw, form).unwrap();
        let text = html_to_text(&raw).unwrap();
        let plain = find_sections(&text, form).unwrap();
        assert_eq!(sections.len(), plain.len());
        let mut rows = 0;
        for (s, p) in sections.iter().zip(&plain) {
            assert_eq!((&s.key, &s.text, s.char_start), (&p.key, &p.text, p.char_start));
            let mut last = 0;
            for t in &s.tables {
                assert!(last <= t.start && t.start < t.end && t.rows.len() >= 2);
                assert!(t.end <= s.text.chars().count());
                for r in &t.rows {
                    rows += 1;
                    let cells: Vec<&str> = r
                        .cells
                        .iter()
                        .filter(|c| !c.text.is_empty())
                        .map(|c| c.text.as_str())
                        .collect();
                    let line = chars(&s.text, r.start, r.end).replace('\n', " ");
                    assert_eq!(line, cells.join(" "));
                }
                last = t.start;
            }
        }
        assert!(rows >= least, "{name}: {rows} rows");
    }
}
