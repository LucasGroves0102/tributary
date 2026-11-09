//! Rules 2-9 of the spec in services/parser/src/filing_parser/sections.py. Offsets and lengths
//! count characters, as Python's str does.

use std::collections::HashMap;

use serde::Serialize;

use crate::Error;
use crate::html::html_to_text;
use crate::pystr::{
    CharIndex, char_len, fold_letter, is_decimal, is_space, is_word, py_strip, upper,
};

pub const PARSER_VERSION: &str = "rust-5"; // 5: clean titles (rule 10); 4: title headings
// (rule 9); 3: MD&A by reference, exhibits, SGML envelope

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Section {
    pub key: String,          // 10-K: "1A"; 10-Q: "II-1A"; 8-K: "2.02"
    pub part: Option<String>, // "I", "II", ... when known
    pub item: String,
    pub title: String,
    pub text: String,
    pub char_start: usize, // offsets into html_to_text(document)
    pub char_end: usize,
}

pub fn parse(raw: &[u8], form: &str) -> Result<Vec<Section>, Error> {
    find_sections(&html_to_text(raw)?, form)
}

/// Rule 8.
pub fn exhibit_section(
    exhibit_type: &str,
    description: Option<&str>,
    raw: &[u8],
) -> Result<Section, Error> {
    let text = html_to_text(raw)?;
    let item = exhibit_type.strip_prefix("EX-").unwrap_or(exhibit_type);
    Ok(Section {
        key: exhibit_type.to_string(),
        part: None,
        item: item.to_string(),
        title: match description {
            Some(d) if !d.is_empty() => d.to_string(),
            _ => format!("Exhibit {item}"),
        },
        char_end: char_len(&text),
        text,
        char_start: 0,
    })
}

#[derive(Debug, Clone)]
struct Heading {
    start: usize,  // byte offset
    cstart: usize, // character offset
    part: Option<String>,
    item: Option<String>, // None for a Part heading
    title: String,
    inline_title: String,
    key: String,
    span: usize,
    title_part: Option<&'static str>, // rule 9 title heading: the Part its title belongs to
    xref: bool,       // Part heading followed by ",": "Part I, Item 2" (rule 9 ignores it)
    title_end: usize, // rule 10: byte offset where the title's line ends ('\n' or the text's end)
}

pub fn find_sections(text: &str, form: &str) -> Result<Vec<Section>, Error> {
    let headings = headings(text);
    if (form.starts_with("10-K") || form.starts_with("10-Q"))
        && headings.iter().all(|h| h.item.is_none())
    {
        // rule 9
        let titles = title_headings(text, form);
        if !titles.is_empty() {
            let mut merged: Vec<Heading> = headings.iter().filter(|h| !h.xref).cloned().collect();
            merged.extend(titles);
            merged.sort_by_key(|h| h.start);
            let body = body(merged, form);
            if body.iter().any(|h| h.item.is_some()) {
                return Ok(sections(text, body, form));
            }
        }
    }
    let body = body(headings, form);
    if body.is_empty() {
        return Err(Error::NoHeadings);
    }
    Ok(sections(text, body, form))
}

/// Rules 5-7 on the body's headings.
fn sections(text: &str, mut body: Vec<Heading>, form: &str) -> Vec<Section> {
    let total = char_len(text);
    for i in 0..body.len() {
        let next = body.get(i + 1).map_or(total, |h| h.cstart);
        body[i].span = next - body[i].cstart;
    }

    let mut best: HashMap<&str, usize> = HashMap::new();
    for (i, h) in body.iter().enumerate() {
        if h.item.is_some() {
            match best.get(h.key.as_str()) {
                Some(&j) if h.span <= body[j].span => {}
                _ => {
                    best.insert(&h.key, i);
                }
            }
        }
    }
    let mut kept: Vec<&Heading> = best.values().map(|&i| &body[i]).collect();
    kept.sort_by_key(|h| h.start);
    let part_starts: Vec<(usize, usize)> = body
        .iter()
        .filter(|h| h.item.is_none())
        .map(|h| (h.start, h.cstart))
        .collect();

    let mut sections = Vec::with_capacity(kept.len());
    for (i, h) in kept.iter().enumerate() {
        let mut end = kept
            .get(i + 1)
            .map_or((text.len(), total), |n| (n.start, n.cstart));
        if let Some(&p) = part_starts.iter().find(|p| h.start < p.0 && p.0 < end.0) {
            end = p;
        }
        sections.push(Section {
            key: h.key.clone(),
            part: h.part.clone(),
            item: h.item.clone().unwrap_or_default(),
            title: section_title(h, text, form),
            text: py_strip(&text[h.start..end.0]).to_string(),
            char_start: h.cstart,
            char_end: end.1,
        });
    }
    if form.starts_with("10-K") {
        for s in &mut sections {
            if s.key == "7" {
                mdna_by_reference(text, s);
            }
        }
    }
    sections
}

/// Rule 7.
fn mdna_by_reference(text: &str, item7: &mut Section) {
    if char_len(&item7.text) >= 2000
        || !item7
            .text
            .to_ascii_lowercase()
            .contains("discussion and analysis")
    {
        return;
    }
    let mut ends = Vec::new();
    let mut mdna = Vec::new();
    for start in line_starts(text) {
        let line = &text[start..];
        let line = &line[..line.find('\n').unwrap_or(line.len())];
        if MDNA_END.iter().any(|p| match_words(line, p).is_some()) {
            ends.push(start);
        }
        if match_words(line, MDNA).is_some() && char_len(line) <= 120 {
            mdna.push(start);
        }
    }
    // closing end line (or len(text)) -> MD&A lines, in first-seen order
    let mut stretches: Vec<(usize, Vec<usize>)> = Vec::new();
    for start in mdna {
        let close = ends
            .iter()
            .copied()
            .find(|&e| e > start)
            .unwrap_or(text.len());
        match stretches.iter_mut().find(|(c, _)| *c == close) {
            Some((_, lines)) => lines.push(start),
            None => stretches.push((close, vec![start])),
        }
    }
    let Some((close, lines)) = stretches
        .into_iter()
        .max_by(|a, b| a.1.len().cmp(&b.1.len()).then(b.0.cmp(&a.0)))
    else {
        return;
    };
    if lines.len() < 2 {
        return;
    }
    let start = lines[0];
    let mut idx = CharIndex::new(text);
    item7.char_start = idx.at(start);
    item7.char_end = idx.at(close);
    item7.text = py_strip(&text[start..close]).to_string();
}

/// `^management[’']s discussion and analysis\b`, IGNORECASE.
const MDNA: &str = "management's discussion and analysis";
/// `^(?:management[’']s report on internal control|report of independent registered public
/// accounting firm)\b`, IGNORECASE.
const MDNA_END: [&str; 2] = [
    "management's report on internal control",
    "report of independent registered public accounting firm",
];

/// Match `pattern` then `\b` at the start of `s`: letters ignore case, `'` is either apostrophe.
fn match_words(s: &str, pattern: &str) -> Option<usize> {
    let mut chars = s.char_indices();
    for p in pattern.chars() {
        let (_, c) = chars.next()?;
        let ok = match p {
            'a'..='z' => fold_letter(c) == Some(p),
            '\'' => c == '\'' || c == '\u{2019}',
            _ => c == p,
        };
        if !ok {
            return None;
        }
    }
    match chars.next() {
        Some((_, c)) if is_word(c) => None,
        Some((i, _)) => Some(i),
        None => Some(s.len()),
    }
}

fn line_starts(text: &str) -> impl Iterator<Item = usize> + '_ {
    std::iter::once(0).chain(text.match_indices('\n').map(|(i, _)| i + 1))
}

/// Rule 2: `_HEADING.finditer(text)`.
fn headings(text: &str) -> Vec<Heading> {
    let mut out = Vec::new();
    let mut idx = CharIndex::new(text);
    let mut resume = 0;
    for start in line_starts(text) {
        if start < resume {
            continue;
        }
        let Some(m) = match_heading(&text[start..]) else {
            continue;
        };
        let end = start + m.end;
        resume = end;
        let cstart = idx.at(start);
        let heading = match m.number {
            Number::Part(numeral) => Heading {
                start,
                cstart,
                part: Some(upper(numeral)),
                item: None,
                title: String::new(),
                inline_title: String::new(),
                key: String::new(),
                span: 0,
                title_part: None,
                xref: m.rest.starts_with(','),
                title_end: 0,
            },
            Number::Item(item) => {
                let inline = py_strip(m.rest).to_string();
                let mut title = inline.as_str();
                let mut title_end = end;
                if title.is_empty() && end < text.len() {
                    // title on the next line, as in many tables of contents
                    let next = &text[end + 1..];
                    let line = next.find('\n').unwrap_or(next.len());
                    title = py_strip(&next[..line]);
                    title_end = end + 1 + line;
                }
                let title = match title.char_indices().nth(200) {
                    Some((i, _)) => title[..i].to_string(),
                    None => title.to_string(),
                };
                Heading {
                    start,
                    cstart,
                    part: None,
                    item: Some(upper(item)),
                    title,
                    inline_title: inline,
                    key: String::new(),
                    span: 0,
                    title_part: None,
                    xref: false,
                    title_end,
                }
            }
        };
        out.push(heading);
    }
    out
}

enum Number<'a> {
    Part(&'a str),
    Item(&'a str),
}

struct HeadingMatch<'a> {
    number: Number<'a>,
    rest: &'a str,
    end: usize,
}

/// `^(?:part\s+(?P<part>iv|i{1,3})\b|item\s+(?P<item>\d{1,2}\.\d{2}|\d{1,2}[a-z]?)\b)
/// [ .:\-–—]*(?P<rest>.*)$`, IGNORECASE, at the start of `s`.
fn match_heading(s: &str) -> Option<HeadingMatch<'_>> {
    let (number, after) = if let Some(n) = word_then_spaces(s, "part") {
        let len = roman(&s[n..])?;
        (Number::Part(&s[n..n + len]), n + len)
    } else {
        let n = word_then_spaces(s, "item")?;
        let len = item_number(&s[n..])?;
        (Number::Item(&s[n..n + len]), n + len)
    };
    let tail = &s[after..];
    let punct = tail
        .char_indices()
        .find(|&(_, c)| !matches!(c, ' ' | '.' | ':' | '-' | '\u{2013}' | '\u{2014}'))
        .map_or(tail.len(), |(i, _)| i);
    let rest = &tail[punct..];
    let rest = &rest[..rest.find('\n').unwrap_or(rest.len())];
    Some(HeadingMatch {
        number,
        end: after + punct + rest.len(),
        rest,
    })
}

/// `word\s+`: the byte offset after the spaces.
fn word_then_spaces(s: &str, word: &str) -> Option<usize> {
    let mut chars = s.char_indices().peekable();
    for w in word.chars() {
        let (_, c) = chars.next()?;
        if fold_letter(c) != Some(w) {
            return None;
        }
    }
    let mut spaces = 0;
    while let Some(&(_, c)) = chars.peek() {
        if !is_space(c) {
            break;
        }
        spaces += 1;
        chars.next();
    }
    if spaces == 0 {
        return None;
    }
    Some(chars.peek().map_or(s.len(), |&(i, _)| i))
}

/// The first 6 characters of a string: enough for any item number and the `\b` after it.
struct Ahead<'a> {
    s: &'a str,
    cs: Vec<(usize, char)>,
}

impl<'a> Ahead<'a> {
    fn new(s: &'a str) -> Self {
        Ahead {
            s,
            cs: s.char_indices().take(6).collect(),
        }
    }

    fn is(&self, n: usize, f: impl Fn(char) -> bool) -> bool {
        self.cs.get(n).is_some_and(|&(_, c)| f(c))
    }

    fn letter(&self, n: usize) -> Option<char> {
        self.cs.get(n).and_then(|&(_, c)| fold_letter(c))
    }

    /// `\b` after `n` word characters.
    fn boundary(&self, n: usize) -> bool {
        !self.is(n, is_word)
    }

    /// Byte offset of character `n`.
    fn offset(&self, n: usize) -> usize {
        self.cs.get(n).map_or(self.s.len(), |&(i, _)| i)
    }
}

/// `(?:iv|i{1,3})\b`: the numeral's length in bytes.
fn roman(s: &str) -> Option<usize> {
    let a = Ahead::new(s);
    if a.letter(0) == Some('i') && a.letter(1) == Some('v') && a.boundary(2) {
        return Some(a.offset(2));
    }
    let run = (0..3).take_while(|&n| a.letter(n) == Some('i')).count();
    (1..=run)
        .rev()
        .find(|&n| a.boundary(n))
        .map(|n| a.offset(n))
}

/// `(?:\d{1,2}\.\d{2}|\d{1,2}[a-z]?)\b`: the item number's length in bytes.
fn item_number(s: &str) -> Option<usize> {
    let a = Ahead::new(s);
    let digits = (0..2).take_while(|&n| a.is(n, is_decimal)).count();
    for d in (1..=digits).rev() {
        if a.is(d, |c| c == '.')
            && a.is(d + 1, is_decimal)
            && a.is(d + 2, is_decimal)
            && a.boundary(d + 3)
        {
            return Some(a.offset(d + 3));
        }
    }
    for d in (1..=digits).rev() {
        if a.letter(d).is_some() && a.boundary(d + 1) {
            return Some(a.offset(d + 1));
        }
        if a.boundary(d) {
            return Some(a.offset(d));
        }
    }
    None
}

/// Headings after the table of contents, with page headers dropped and keys assigned.
fn body(mut headings: Vec<Heading>, form: &str) -> Vec<Heading> {
    // rule 3: find where the body starts
    let mut seen = std::collections::HashSet::new();
    let mut part: Option<String> = None;
    let mut start = 0;
    for i in 0..headings.len() {
        let h = &headings[i];
        let Some(item) = &h.item else {
            part = h.part.clone();
            continue;
        };
        if let (Some(tp), Some(p)) = (h.title_part, &part)
            && tp != p
        {
            continue;
        }
        let key = key(form, h.title_part.or(part.as_deref()), item);
        if seen.contains(&key) {
            start = i;
            if i > 0 && headings[i - 1].item.is_none() && h.cstart - headings[i - 1].cstart <= 50 {
                start = i - 1;
            }
            break;
        }
        seen.insert(key);
    }

    // rule 4: walk the body
    let ten_q = form.starts_with("10-Q");
    let mut body: Vec<Heading> = Vec::new();
    let mut part: Option<String> = None;
    let mut current: Option<(String, String)> = None; // key and inline title
    for mut h in headings.drain(start..) {
        let Some(item) = h.item.clone() else {
            if h.part == part {
                continue;
            }
            part = h.part.clone();
            body.push(h);
            continue;
        };
        if let Some(tp) = h.title_part {
            if part.as_deref().is_some_and(|p| p != tp) {
                continue;
            }
            h.part = Some(tp.to_string());
        } else {
            h.part = part.clone().or_else(|| ten_q.then(|| "I".to_string()));
        }
        h.key = key(form, h.part.as_deref(), &item);
        if let Some((key, inline)) = &current
            && h.key == *key
            && (h.inline_title.is_empty() || h.inline_title == *inline)
        {
            continue;
        }
        current = Some((h.key.clone(), h.inline_title.clone()));
        body.push(h);
    }
    body
}

/// Rule 9: lines that are a standard item title.
fn title_headings(text: &str, form: &str) -> Vec<Heading> {
    let titles = if form.starts_with("10-Q") {
        TITLES_10Q
    } else {
        TITLES_10K
    };
    let mut out = Vec::new();
    let mut idx = CharIndex::new(text);
    for start in line_starts(text) {
        let line = &text[start..];
        let line = &line[..line.find('\n').unwrap_or(line.len())];
        if char_len(line) > 120 {
            continue;
        }
        let folded: String = title_core(line).chars().map(fold_title).collect();
        let Some(&(_, part, item)) = titles.iter().find(|(t, _, _)| *t == folded) else {
            continue;
        };
        out.push(Heading {
            start,
            cstart: idx.at(start),
            part: None,
            item: Some(item.to_string()),
            title: line.to_string(),
            inline_title: String::new(),
            key: String::new(),
            span: 0,
            title_part: Some(part),
            xref: false,
            title_end: start + line.len(),
        });
    }
    out
}

/// Rule 9's core of a line: without one trailing "." or ":", then without a final
/// parenthetical of 1-20 characters and one space before it.
fn title_core(line: &str) -> &str {
    let line = line
        .strip_suffix('.')
        .or_else(|| line.strip_suffix(':'))
        .unwrap_or(line);
    if let Some(inner) = line.strip_suffix(')')
        && let Some(i) = inner.rfind('(')
    {
        let between = &inner[i + 1..];
        if (1..=20).contains(&char_len(between)) && !between.contains(')') {
            let head = &line[..i];
            return head.strip_suffix(' ').unwrap_or(head);
        }
    }
    line
}

/// Rule 9's fold: `_FOLD` in sections.py.
fn fold_title(c: char) -> char {
    if c == '\u{2019}' {
        return '\'';
    }
    fold_letter(c).unwrap_or(c)
}

/// Rule 10a: `_TITLE_LEAD` in sections.py, what a title may not start with besides whitespace:
/// these, U+2010-U+2015 (hyphens and dashes) and U+200B-U+200F, U+FEFF (invisible).
const TITLE_LEAD: &str = ".,:;|-*)\\\u{b7}\u{201d}\u{2019}\u{2018}\u{2026}";

fn title_lead(c: char) -> bool {
    let dash = ('\u{2010}'..='\u{2015}').contains(&c);
    let invisible = ('\u{200b}'..='\u{200f}').contains(&c) || c == '\u{feff}';
    is_space(c) || dash || invisible || TITLE_LEAD.contains(c)
}

/// Rule 10: the section's title from its heading's.
fn section_title(h: &Heading, text: &str, form: &str) -> String {
    let mut t = h.title.trim_start_matches(title_lead).to_string();
    let quotes = [('"', '"'), ('\u{201c}', '\u{201d}')];
    if let Some(&(open, close)) = quotes.iter().find(|q| t.starts_with(q.0)) {
        let rest = &t[open.len_utf8()..];
        t = match rest.find(close) {
            Some(i) => rest[..i].trim_end_matches([',', ' ']).to_string(),
            None => rest.to_string(),
        };
    }
    if t.ends_with('\u{2026}') || t.ends_with("..") {
        t = t
            .trim_end_matches(['.', '\u{2026}'])
            .trim_end_matches(' ')
            .to_string();
    }
    let wanted = wanted_titles(h, form);
    let mut words = title_words(&t);
    let mut end = h.title_end;
    for _ in 0..3 {
        let shorter = |w: &Vec<&str>| w.len() > words.len() && starts(w, &words);
        if words.is_empty() || end >= text.len() || !wanted.iter().any(shorter) {
            break;
        }
        let rest = &text[end + 1..];
        let line = &rest[..rest.find('\n').unwrap_or(rest.len())];
        let joined = format!("{t} {line}");
        let more = title_words(&joined);
        if line.is_empty()
            || char_len(line) > 120
            || more.len() <= words.len()
            || !wanted.iter().any(|w| starts(w, &more))
        {
            break;
        }
        t = joined;
        words = more;
        end += 1 + line.len();
    }
    match t.char_indices().nth(200) {
        Some((i, _)) => t[..i].to_string(),
        None => t,
    }
}

/// Rule 10d: the words of the titles a wrapped title may be the start of: TITLES_10K with its
/// item, TITLES_10Q with its Part and item; none for other forms.
fn wanted_titles(h: &Heading, form: &str) -> Vec<Vec<&'static str>> {
    let q = form.starts_with("10-Q");
    if !q && !form.starts_with("10-K") {
        return Vec::new();
    }
    let Some(item) = h.item.as_deref() else {
        return Vec::new();
    };
    let part = h.part.as_deref();
    let titles = if q { TITLES_10Q } else { TITLES_10K };
    titles
        .iter()
        .filter(|t| t.2 == item && (!q || Some(t.1) == part))
        .map(|t| t.0.split(' ').collect())
        .collect()
}

/// Rule 10d: rule 9's core of a title, folded, split on whitespace (`_title_words`).
fn title_words(title: &str) -> Vec<String> {
    let folded: String = title_core(title).chars().map(fold_title).collect();
    folded
        .split(is_space)
        .filter(|w| !w.is_empty())
        .map(str::to_string)
        .collect()
}

/// Whether `words` are the start of (or all of) a title's words.
fn starts(title: &[&str], words: &[String]) -> bool {
    title.len() >= words.len() && title[..words.len()] == *words
}

const MDNA_1: &str =
    "management's discussion and analysis of financial condition and results of operations";
const MDNA_2: &str =
    "management's discussion and analysis of results of operations and financial condition";
const MARKET_RISK_1: &str = "quantitative and qualitative disclosures about market risk";
const MARKET_RISK_2: &str = "quantitative and qualitative disclosure about market risk";

/// Rule 9: `TITLES_10K` in sections.py, folded title -> (Part, item).
const TITLES_10K: &[(&str, &str, &str)] = &[
    ("business", "I", "1"),
    ("risk factors", "I", "1A"),
    ("unresolved staff comments", "I", "1B"),
    ("cybersecurity", "I", "1C"),
    ("properties", "I", "2"),
    ("legal proceedings", "I", "3"),
    ("mine safety disclosures", "I", "4"),
    (
        "market for registrant's common equity, related stockholder matters and issuer \
         purchases of equity securities",
        "II",
        "5",
    ),
    ("selected financial data", "II", "6"),
    (MDNA_1, "II", "7"),
    (MDNA_2, "II", "7"),
    (MARKET_RISK_1, "II", "7A"),
    (MARKET_RISK_2, "II", "7A"),
    ("financial statements and supplementary data", "II", "8"),
    (
        "changes in and disagreements with accountants on accounting and financial disclosure",
        "II",
        "9",
    ),
    ("controls and procedures", "II", "9A"),
    ("other information", "II", "9B"),
    (
        "disclosure regarding foreign jurisdictions that prevent inspections",
        "II",
        "9C",
    ),
    (
        "directors, executive officers and corporate governance",
        "III",
        "10",
    ),
    ("executive compensation", "III", "11"),
    (
        "security ownership of certain beneficial owners and management and related \
         stockholder matters",
        "III",
        "12",
    ),
    (
        "certain relationships and related transactions, and director independence",
        "III",
        "13",
    ),
    ("principal accountant fees and services", "III", "14"),
    ("exhibits and financial statement schedules", "IV", "15"),
    ("exhibit and financial statement schedules", "IV", "15"),
    ("exhibits, financial statement schedules", "IV", "15"),
    ("form 10-k summary", "IV", "16"),
];

/// Rule 9: `TITLES_10Q` in sections.py.
const TITLES_10Q: &[(&str, &str, &str)] = &[
    ("financial statements", "I", "1"),
    ("consolidated financial statements", "I", "1"),
    ("condensed consolidated financial statements", "I", "1"),
    (
        "unaudited condensed consolidated financial statements",
        "I",
        "1",
    ),
    ("unaudited consolidated financial statements", "I", "1"),
    (MDNA_1, "I", "2"),
    (MDNA_2, "I", "2"),
    (MARKET_RISK_1, "I", "3"),
    (MARKET_RISK_2, "I", "3"),
    ("controls and procedures", "I", "4"),
    ("legal proceedings", "II", "1"),
    ("risk factors", "II", "1A"),
    (
        "unregistered sales of equity securities and use of proceeds",
        "II",
        "2",
    ),
    ("defaults upon senior securities", "II", "3"),
    ("mine safety disclosures", "II", "4"),
    ("other information", "II", "5"),
    ("exhibits", "II", "6"),
];

fn key(form: &str, part: Option<&str>, item: &str) -> String {
    // 10-Q item numbers restart in each Part; 10-K and 8-K item numbers are unique
    if form.starts_with("10-Q") {
        format!("{}-{}", part.unwrap_or("?"), item)
    } else {
        item.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn heading_patterns() {
        fn m(s: &str) -> Option<(usize, &str)> {
            match_heading(s).map(|m| (m.end, m.rest))
        }
        assert_eq!(m("Item 1A. Risk Factors"), Some((21, "Risk Factors")));
        assert_eq!(m("ITEM\n7 \u{2014} MD&A\nx"), Some((15, "MD&A")));
        assert_eq!(m("Item 2.02 Results"), Some((17, "Results")));
        assert_eq!(m("Item 123"), None);
        assert_eq!(m("Item 7of"), None);
        assert!(matches!(
            match_heading("Part IV"),
            Some(HeadingMatch {
                number: Number::Part("IV"),
                ..
            })
        ));
        assert!(matches!(
            match_heading("PART III."),
            Some(HeadingMatch {
                number: Number::Part("III"),
                ..
            })
        ));
        assert!(match_heading("Part IIII").is_none());
        assert!(match_heading("Partial").is_none());
    }
}
