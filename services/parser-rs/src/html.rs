//! `html_to_text`: rule 1 of the spec, matching the Python parser byte for byte.
//!
//! The Python parser builds its tree with `lxml.html.fromstring` (lxml 6.0, libxml2 2.14), so
//! this module ports the parts of libxml2's HTMLparser.c and SAX2.c that shape the tree:
//! tokenizer, implied and auto-closed elements, end tag priorities and the 256-level depth
//! limit, plus the root selection of `lxml.html.fromstring`. Function names in comments refer
//! to libxml2 and lxml.

use std::borrow::Cow;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

use serde::Serialize;

use crate::Error;
use crate::pystr::{char_len, collapse_spaces, is_space, py_strip};
use crate::tables;

/// htmlElemDesc.dataMode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DataMode {
    Rcdata = 1,
    Rawtext = 2,
    Plaintext = 3,
    Script = 4,
}

const MODE_DATA: u8 = 0;
const MODE_RCDATA: u8 = DataMode::Rcdata as u8;
const MODE_PLAINTEXT: u8 = DataMode::Plaintext as u8;
const MODE_SCRIPT: u8 = DataMode::Script as u8;
const MODE_SCRIPT_ESC1: u8 = 5;
const MODE_SCRIPT_ESC2: u8 = 6;

/// nodePush fails beyond this many open elements without XML_PARSE_HUGE.
const MAX_DEPTH: usize = 256;
/// XML_MAX_TEXT_LENGTH: xmlSAX2Text halts the parser when a text node grows beyond it.
const MAX_TEXT: usize = 10_000_000;
/// HTML_PARSER_BUFFER_SIZE: htmlParseHTMLName keeps at most this many bytes of a name.
const MAX_NAME: usize = 100;

const INSERT_IN_HEAD: u8 = 3;
const INSERT_IN_BODY: u8 = 10;

const BLOCK: &[&str] = &[
    "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section",
];
const CELL: &[&str] = &["td", "th"];
const DROP: &[&str] = &["title", "script", "style", "header"];

const FFFD: &[u8] = "\u{FFFD}".as_bytes();

/// Rule 1: the text of an HTML document, one line per block.
pub fn html_to_text(raw: &[u8]) -> Result<String, Error> {
    Ok(extract_with(raw, false)?.0)
}

/// Rule 1's text and rule 11's tables of the whole document, row offsets into that text.
pub fn extract(raw: &[u8]) -> Result<(String, Vec<Table>), Error> {
    let (text, tables, _) = extract_with(raw, true)?;
    Ok((text, tables))
}

/// Rule 1's text, rule 11's tables and rule 14's printed facts (sections not yet assigned).
pub fn extract_all(raw: &[u8]) -> Result<(String, Vec<Table>, Vec<PrintedFact>), Error> {
    extract_with(raw, true)
}

type Extracted = (String, Vec<Table>, Vec<PrintedFact>);

fn extract_with(raw: &[u8], tables: bool) -> Result<Extracted, Error> {
    let raw = strip_envelope(raw);
    let full = looks_like_full_html(raw);
    let mut dom = Parser::new(raw).parse();
    let (root, root_tail) = select_root(&mut dom, full)?;
    if !matches!(dom.node(root).kind, Kind::Element(..)) {
        return Err(Error::NotAnElement);
    }
    let header = if tables {
        read_header(&dom, root)
    } else {
        Header::default()
    };
    drop_hidden(&mut dom)?;
    let mut walk = Walk::default();
    let (content, exact) = dom.text_content(root, root_tail, tables.then_some(&mut walk))?;
    let mut out = String::with_capacity(content.len());
    let mut lines = Vec::new();
    let mut at = 0; // characters in `out`
    let mut raw_at = 0;
    for line in content.split('\n') {
        let collapsed = collapse_spaces(line);
        let kept = py_strip(&collapsed);
        let mut info = Line {
            raw: raw_at,
            kept: None,
        };
        raw_at += line.len() + 1;
        if !kept.is_empty() {
            if !out.is_empty() {
                out.push('\n');
                at += 1;
            }
            let leadb = collapsed.len() - collapsed.trim_start_matches(is_space).len();
            let size = char_len(kept);
            info.kept = Some(Kept {
                at,
                byte: out.len(),
                lead: char_len(&collapsed[..leadb]),
                lead_bytes: leadb,
                size,
                size_bytes: kept.len(),
            });
            out.push_str(kept);
            at += size;
        }
        if tables {
            lines.push(info);
        }
    }
    if !tables || !exact {
        return Ok((out, Vec::new(), Vec::new()));
    }
    let facts = if walk.facts.is_empty() {
        Vec::new()
    } else {
        printed_facts(&content, &lines, &out, &walk.facts, &dom, &header)
    };
    if walk.tables.is_empty() {
        return Ok((out, Vec::new(), facts));
    }
    let found = rows_and_cells(&content, &lines, &out, walk.tables);
    Ok((out, found, facts))
}

// ---------------------------------------------------------------------------------------------
// Rule 14: printed facts beside the text

/// An inline XBRL numeric fact (ix:nonFraction) where the text prints it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PrintedFact {
    pub section: Option<String>, // the key of the section whose text holds it
    pub concept: String,         // its name attribute: "us-gaap:Revenues"
    pub context: String,         // contextref
    pub unit_ref: Option<String>,
    pub unit: Option<String>,  // the unit's measure: "USD", "USD/shares"
    pub start: Option<String>, // the period's start (a duration), none for an instant
    pub end: String,           // the period's end, or the instant
    pub scale: Option<String>, // attributes as written
    pub sign: Option<String>,
    pub decimals: Option<String>,
    pub format: Option<String>,
    pub text: String,      // its characters in the text, '\n' read as ' '
    pub char_start: usize, // [char_start, char_end) in characters of the document's text
    pub char_end: usize,
}

/// What a name is to rule 14 (by rule 1's reading of it, ending in ":<this>").
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Ix {
    None,
    Fact,
    Context,
    Unit,
    Measure,
    Divide,
    StartDate,
    EndDate,
    Instant,
    Member,
}

impl Ix {
    fn of(local: &str) -> Ix {
        let Some((_, last)) = local.rsplit_once(':') else {
            return Ix::None;
        };
        match last {
            "nonfraction" => Ix::Fact,
            "context" => Ix::Context,
            "unit" => Ix::Unit,
            "measure" => Ix::Measure,
            "divide" => Ix::Divide,
            "startdate" => Ix::StartDate,
            "enddate" => Ix::EndDate,
            "instant" => Ix::Instant,
            "explicitmember" | "typedmember" => Ix::Member,
            _ => Ix::None,
        }
    }

    /// Elements whose attributes rule 14 reads.
    fn keeps_attributes(self) -> bool {
        matches!(self, Ix::Fact | Ix::Context | Ix::Unit)
    }
}

/// An element's (name, value) attributes as rule 14 keeps them.
type Attributes = Vec<(Box<[u8]>, Box<[u8]>)>;

/// The attributes rule 14 reads.
const IX_ATTRIBUTES: &[&[u8]] = &[
    b"id",
    b"name",
    b"contextref",
    b"unitref",
    b"scale",
    b"sign",
    b"decimals",
    b"format",
];

/// A context (rule 14a): start, end, instant, and whether it is dimensioned.
#[derive(Default)]
struct Context {
    start: Option<String>,
    end: Option<String>,
    instant: Option<String>,
    dimensioned: bool,
}

/// Rule 14a's contexts and units, by id.
#[derive(Default)]
struct Header {
    contexts: HashMap<String, Context>,
    units: HashMap<String, Option<String>>,
}

/// A "*:nonfraction" element's [start, end) byte range in the content.
struct RawFact {
    node: u32,
    start: usize,
    end: usize,
}

/// Rule 14a on `root` and its descendants, before the drops.
fn read_header(dom: &Dom, root: u32) -> Header {
    let mut header = Header::default();
    for id in dom.subtree(root) {
        let ix = dom.ix(id);
        if !matches!(ix, Ix::Context | Ix::Unit) {
            continue;
        }
        let Some(key) = dom.attr(id, b"id") else {
            continue;
        };
        if ix == Ix::Context {
            header
                .contexts
                .entry(key)
                .or_insert_with(|| read_context(dom, id));
        } else {
            header
                .units
                .entry(key)
                .or_insert_with(|| read_unit(dom, id));
        }
    }
    header
}

fn read_context(dom: &Dom, id: u32) -> Context {
    let mut c = Context::default();
    for d in dom.subtree(id).skip(1) {
        let slot = match dom.ix(d) {
            Ix::StartDate => &mut c.start,
            Ix::EndDate => &mut c.end,
            Ix::Instant => &mut c.instant,
            Ix::Member => {
                c.dimensioned = true;
                continue;
            }
            _ => continue,
        };
        if slot.is_none() {
            *slot = Some(py_strip(&dom.subtree_text(d)).to_string());
        }
    }
    c
}

fn read_unit(dom: &Dom, id: u32) -> Option<String> {
    let mut measures: Vec<String> = Vec::new();
    let mut divide = false;
    for d in dom.subtree(id).skip(1) {
        match dom.ix(d) {
            Ix::Measure => {
                let text = dom.subtree_text(d);
                let m = py_strip(&text);
                measures.push(m.rsplit(':').next().unwrap_or("").to_string());
            }
            Ix::Divide => divide = true,
            _ => {}
        }
    }
    if divide && measures.len() == 2 {
        return Some(format!("{}/{}", measures[0], measures[1]));
    }
    if measures.len() == 1 {
        return measures.pop();
    }
    None
}

/// Rule 14b: the facts printed in the text, offsets into the whole text.
fn printed_facts(
    content: &str,
    lines: &[Line],
    text: &str,
    raw: &[RawFact],
    dom: &Dom,
    header: &Header,
) -> Vec<PrintedFact> {
    let mut positions: Vec<usize> = raw.iter().flat_map(|f| [f.start, f.end]).collect();
    positions.sort_unstable();
    positions.dedup();
    let landed = land(content, lines, text, &positions);
    let spot = |p: usize| landed[positions.binary_search(&p).unwrap_or(0)];
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    for f in raw {
        let Some(concept) = dom.attr(f.node, b"name").filter(|n| !n.is_empty()) else {
            continue;
        };
        let Some(reference) = dom.attr(f.node, b"contextref") else {
            continue;
        };
        let Some(context) = header.contexts.get(&reference) else {
            continue;
        };
        if context.dimensioned {
            continue;
        }
        let (start, end) = match (&context.end, &context.instant) {
            (Some(e), _) if !e.is_empty() => (context.start.clone(), e.clone()),
            (_, Some(i)) if !i.is_empty() => (None, i.clone()),
            _ => continue,
        };
        let (mut a, _) = spot(f.start);
        let (_, mut b) = spot(f.end);
        while a.0 < b.0 && bytes[a.1] == b' ' {
            a = (a.0 + 1, a.1 + 1);
        }
        while b.0 > a.0 && bytes[b.1 - 1] == b' ' {
            b = (b.0 - 1, b.1 - 1);
        }
        if a.0 >= b.0 {
            continue;
        }
        let unit_ref = dom.attr(f.node, b"unitref");
        let unit = unit_ref
            .as_ref()
            .and_then(|u| header.units.get(u))
            .cloned()
            .flatten();
        out.push(PrintedFact {
            section: None,
            concept,
            context: reference,
            unit_ref,
            unit,
            start,
            end,
            scale: dom.attr(f.node, b"scale"),
            sign: dom.attr(f.node, b"sign"),
            decimals: dom.attr(f.node, b"decimals"),
            format: dom.attr(f.node, b"format"),
            text: text[a.1..b.1].replace('\n', " "),
            char_start: a.0,
            char_end: b.0,
        });
    }
    out
}

// ---------------------------------------------------------------------------------------------
// Rule 11: tables beside the text

/// One `<td>` or `<th>`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Cell {
    pub text: String, // its characters in the text, '\n' read as ' '; "" when it has none
    pub span: u32,    // its colspan, 1-100
}

/// One `<tr>` with at least one non-empty cell.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Row {
    pub start: usize, // [start, end) in characters: its first to last non-empty cell
    pub end: usize,
    pub head: bool, // inside a <thead> of its table, or every non-empty cell a <th>
    pub cells: Vec<Cell>,
}

/// One `<table>`'s rows (in a section: those that lie in it).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Table {
    pub start: usize, // [start, end) in characters: its rows' smallest start to largest end
    pub end: usize,
    pub rows: Vec<Row>,
}

/// What a name is to rule 11.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TablePart {
    None,
    Table,
    Thead,
    Tr,
    Td,
    Th,
}

/// A cell's [start, end) byte range in the content (text_content()), colspan and kind.
struct RawCell {
    start: usize,
    end: usize,
    span: u32,
    th: bool,
}

struct RawRow {
    head: bool,
    cells: Vec<RawCell>,
}

/// An open `<table>`: its index, its innermost open row, how many `<thead>`s are open in it.
struct OpenTable {
    table: usize,
    row: Option<usize>,
    theads: u32,
}

/// What leaving an entered element undoes.
enum Frame {
    Other,
    Table,
    Thead(usize),
    Tr(usize, Option<usize>),
    Cell(usize, usize, usize),
    Fact(usize),
}

/// Rule 11a while text_content() walks the tree: tables in document order, each its rows;
/// and rule 14b's "*:nonfraction" elements in document order.
#[derive(Default)]
struct Walk {
    tables: Vec<Vec<RawRow>>,
    open: Vec<OpenTable>,
    frames: Vec<Frame>,
    facts: Vec<RawFact>,
}

impl Walk {
    fn enter(&mut self, part: TablePart, span: u32, at: usize, fact: Option<u32>) {
        if let Some(node) = fact {
            self.facts.push(RawFact {
                node,
                start: at,
                end: at,
            });
            self.frames.push(Frame::Fact(self.facts.len() - 1));
            return;
        }
        let frame = match part {
            TablePart::Table => {
                self.tables.push(Vec::new());
                self.open.push(OpenTable {
                    table: self.tables.len() - 1,
                    row: None,
                    theads: 0,
                });
                Frame::Table
            }
            _ if self.open.is_empty() => Frame::Other,
            TablePart::Thead => {
                let o = self.open.len() - 1;
                self.open[o].theads += 1;
                Frame::Thead(o)
            }
            TablePart::Tr => {
                let o = self.open.len() - 1;
                let t = self.open[o].table;
                self.tables[t].push(RawRow {
                    head: self.open[o].theads > 0,
                    cells: Vec::new(),
                });
                let saved = self.open[o].row.replace(self.tables[t].len() - 1);
                Frame::Tr(o, saved)
            }
            TablePart::Td | TablePart::Th => match self.open.last() {
                Some(&OpenTable {
                    table,
                    row: Some(row),
                    ..
                }) => {
                    let cells = &mut self.tables[table][row].cells;
                    cells.push(RawCell {
                        start: at,
                        end: at,
                        span,
                        th: part == TablePart::Th,
                    });
                    Frame::Cell(table, row, cells.len() - 1)
                }
                _ => Frame::Other,
            },
            TablePart::None => Frame::Other,
        };
        self.frames.push(frame);
    }

    fn exit(&mut self, at: usize) {
        match self.frames.pop() {
            Some(Frame::Table) => {
                self.open.pop();
            }
            Some(Frame::Thead(o)) => self.open[o].theads -= 1,
            Some(Frame::Tr(o, saved)) => self.open[o].row = saved,
            Some(Frame::Cell(t, r, c)) => self.tables[t][r].cells[c].end = at,
            Some(Frame::Fact(i)) => self.facts[i].end = at,
            Some(Frame::Other) | None => {}
        }
    }
}

/// A content line: its first byte, and where it is in the text when rule 1 keeps it.
struct Line {
    raw: usize,
    kept: Option<Kept>,
}

/// A kept line: its start in the text (characters, bytes), the characters (bytes) strip() took
/// from its collapsed form's start, and its length.
#[derive(Clone, Copy)]
struct Kept {
    at: usize,
    byte: usize,
    lead: usize,
    lead_bytes: usize,
    size: usize,
    size_bytes: usize,
}

/// A text position: (characters, bytes).
type Spot = (usize, usize);

/// `_SPACES`' characters (pystr::collapse_spaces).
fn collapsible(c: char) -> bool {
    matches!(
        c,
        ' ' | '\t' | '\u{a0}' | '\u{200b}' | '\u{2009}' | '\u{202f}'
    )
}

/// Rule 11b for every sorted content byte offset: the first text character that comes from
/// it or later (the text's end when none), and one past the last that comes from before it
/// (0 when none). A collapsed space comes from its run's first character.
fn land(content: &str, lines: &[Line], text: &str, positions: &[usize]) -> Vec<(Spot, Spot)> {
    let total = (char_len(text), text.len());
    let mut next = vec![total; lines.len() + 1];
    for (i, line) in lines.iter().enumerate().rev() {
        next[i] = line.kept.map_or(next[i + 1], |k| (k.at, k.byte));
    }
    let mut prev = Vec::with_capacity(lines.len());
    let mut end = (0, 0);
    for line in lines {
        prev.push(end);
        if let Some(k) = line.kept {
            end = (k.at + k.size, k.byte + k.size_bytes);
        }
    }
    let mut out = Vec::with_capacity(positions.len());
    let mut i = 0;
    let mut q = 0;
    while q < positions.len() {
        while i + 1 < lines.len() && lines[i + 1].raw <= positions[q] {
            i += 1;
        }
        let start = lines[i].raw;
        let stop = lines.get(i + 1).map_or(content.len(), |l| l.raw - 1);
        // characters (bytes) of the collapsed line that come from before the scan's position
        let (mut c, mut cb, mut in_run) = (0, 0, false);
        let mut chars = content[start..stop].char_indices().peekable();
        while q < positions.len() && positions[q] <= stop {
            let p = positions[q] - start;
            while let Some(&(b, ch)) = chars.peek() {
                if b >= p {
                    break;
                }
                if collapsible(ch) {
                    if !in_run {
                        c += 1;
                        cb += 1;
                    }
                    in_run = true;
                } else {
                    c += 1;
                    cb += ch.len_utf8();
                    in_run = false;
                }
                chars.next();
            }
            out.push(match lines[i].kept {
                Some(k) => {
                    let first = if c <= k.lead {
                        (k.at, k.byte)
                    } else if c - k.lead < k.size {
                        (k.at + c - k.lead, k.byte + cb - k.lead_bytes)
                    } else {
                        next[i + 1]
                    };
                    let last = if c <= k.lead {
                        prev[i]
                    } else if c - k.lead >= k.size {
                        (k.at + k.size, k.byte + k.size_bytes)
                    } else {
                        (k.at + c - k.lead, k.byte + cb - k.lead_bytes)
                    };
                    (first, last)
                }
                None => (next[i + 1], prev[i]),
            });
            q += 1;
        }
    }
    out
}

/// Rule 11b: each table's rows with text, their cells and offsets into the whole text.
fn rows_and_cells(content: &str, lines: &[Line], text: &str, raw: Vec<Vec<RawRow>>) -> Vec<Table> {
    let mut positions: Vec<usize> = raw
        .iter()
        .flatten()
        .flat_map(|r| r.cells.iter().flat_map(|c| [c.start, c.end]))
        .collect();
    positions.sort_unstable();
    positions.dedup();
    let landed = land(content, lines, text, &positions);
    let spot = |p: usize| landed[positions.binary_search(&p).unwrap_or(0)];
    let bytes = text.as_bytes();
    let mut tables = Vec::new();
    for rows in raw {
        let mut kept = Vec::new();
        for row in rows {
            let mut cells = Vec::with_capacity(row.cells.len());
            let mut range: Option<(usize, usize)> = None;
            let mut all_th = true;
            for c in &row.cells {
                let (mut a, _) = spot(c.start);
                let (_, mut b) = spot(c.end);
                while a.0 < b.0 && bytes[a.1] == b' ' {
                    a = (a.0 + 1, a.1 + 1);
                }
                while b.0 > a.0 && bytes[b.1 - 1] == b' ' {
                    b = (b.0 - 1, b.1 - 1);
                }
                let mut cell_text = String::new();
                if a.0 < b.0 {
                    cell_text = text[a.1..b.1].replace('\n', " ");
                    range = Some(range.map_or((a.0, b.0), |(s, e)| (s.min(a.0), e.max(b.0))));
                    all_th &= c.th;
                }
                cells.push(Cell {
                    text: cell_text,
                    span: c.span,
                });
            }
            if let Some((start, end)) = range {
                kept.push(Row {
                    start,
                    end,
                    head: row.head || all_th,
                    cells,
                });
            }
        }
        if !kept.is_empty() {
            let start = kept.iter().map(|r| r.start).min().unwrap_or(0);
            let end = kept.iter().map(|r| r.end).max().unwrap_or(0);
            tables.push(Table {
                start,
                end,
                rows: kept,
            });
        }
    }
    tables
}

/// Rule 11a: a colspan attribute's ASCII digits after any of " \t\n\r\f" at its start, capped
/// to 1-100; 1 without such digits.
fn colspan(value: Option<&[u8]>) -> u32 {
    let v = value.unwrap_or_default();
    let lead = v
        .iter()
        .take_while(|&&c| matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0c))
        .count();
    let mut n: Option<u32> = None;
    for &d in v[lead..].iter().take_while(|c| c.is_ascii_digit()) {
        n = Some((n.unwrap_or(0) * 10 + u32::from(d - b'0')).min(1000));
    }
    n.map_or(1, |n| n.clamp(1, 100))
}

/// `re.sub(rb"^\s*<DOCUMENT>.*?<TEXT>", b"", raw, count=1, flags=re.I | re.S)`.
fn strip_envelope(raw: &[u8]) -> &[u8] {
    let start = raw
        .iter()
        .position(|c| !is_py_bytes_space(*c))
        .unwrap_or(raw.len());
    let rest = &raw[start..];
    if !starts_with_ignore_case(rest, b"<document>") {
        return raw;
    }
    match find_ignore_case(&rest[10..], b"<text>") {
        Some(i) => &rest[10 + i + 6..],
        None => raw,
    }
}

/// lxml.html `_looks_like_full_html_bytes`: `^\s*<(?:html|!doctype)`, ignoring case.
fn looks_like_full_html(raw: &[u8]) -> bool {
    let start = raw
        .iter()
        .position(|c| !is_py_bytes_space(*c))
        .unwrap_or(raw.len());
    let rest = &raw[start..];
    starts_with_ignore_case(rest, b"<html") || starts_with_ignore_case(rest, b"<!doctype")
}

fn is_py_bytes_space(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)
}

fn starts_with_ignore_case(s: &[u8], prefix: &[u8]) -> bool {
    s.len() >= prefix.len() && s[..prefix.len()].eq_ignore_ascii_case(prefix)
}

fn find_ignore_case(s: &[u8], needle: &[u8]) -> Option<usize> {
    s.windows(needle.len())
        .position(|w| w.eq_ignore_ascii_case(needle))
}

// ---------------------------------------------------------------------------------------------
// Tree

const NIL: u32 = u32::MAX;

#[derive(Debug, Clone, Copy)]
enum Kind {
    Document,
    /// Name id, and whether rule 1 drops it (title, script, style, header, display:none).
    Element(u32, bool),
    /// Byte range in `Dom::text`.
    Text(u32, u32),
    Comment,
}

#[derive(Debug, Clone, Copy)]
struct Node {
    kind: Kind,
    parent: u32,
    first: u32,
    last: u32,
    prev: u32,
    next: u32,
}

struct Dom {
    nodes: Vec<Node>,
    text: Vec<u8>,
    names: Names,
    /// Cells' colspans other than 1 (rule 11a), by node.
    spans: HashMap<u32, u32, BuildHasherDefault<Fnv>>,
    /// Rule 14's attributes (IX_ATTRIBUTES) of facts, contexts and units, by node: the first of
    /// each name, a valueless one as "".
    attrs: HashMap<u32, Attributes, BuildHasherDefault<Fnv>>,
}

impl Dom {
    fn new() -> Self {
        let doc = Node {
            kind: Kind::Document,
            parent: NIL,
            first: NIL,
            last: NIL,
            prev: NIL,
            next: NIL,
        };
        Dom {
            nodes: vec![doc],
            text: Vec::new(),
            names: Names::new(),
            spans: HashMap::default(),
            attrs: HashMap::default(),
        }
    }

    fn node(&self, id: u32) -> &Node {
        &self.nodes[id as usize]
    }

    fn create(&mut self, kind: Kind) -> u32 {
        let id = self.nodes.len() as u32;
        self.nodes.push(Node {
            kind,
            parent: NIL,
            first: NIL,
            last: NIL,
            prev: NIL,
            next: NIL,
        });
        id
    }

    fn append(&mut self, parent: u32, child: u32) {
        let last = self.node(parent).last;
        if last == NIL {
            self.nodes[parent as usize].first = child;
        } else {
            self.nodes[last as usize].next = child;
        }
        let c = &mut self.nodes[child as usize];
        c.prev = last;
        c.next = NIL;
        c.parent = parent;
        self.nodes[parent as usize].last = child;
    }

    fn unlink(&mut self, id: u32) {
        let Node {
            parent, prev, next, ..
        } = *self.node(id);
        if parent == NIL {
            return;
        }
        if prev == NIL {
            self.nodes[parent as usize].first = next;
        } else {
            self.nodes[prev as usize].next = next;
        }
        if next == NIL {
            self.nodes[parent as usize].last = prev;
        } else {
            self.nodes[next as usize].prev = prev;
        }
        let n = &mut self.nodes[id as usize];
        n.parent = NIL;
        n.prev = NIL;
        n.next = NIL;
    }

    /// `root` and its descendants in document order.
    fn subtree(&self, root: u32) -> impl Iterator<Item = u32> + '_ {
        let mut next = Some(root);
        std::iter::from_fn(move || {
            let id = next?;
            let n = self.node(id);
            next = if n.first != NIL {
                Some(n.first)
            } else {
                let mut cur = id;
                loop {
                    if cur == root {
                        break None;
                    }
                    let n = self.node(cur);
                    if n.next != NIL {
                        break Some(n.next);
                    }
                    cur = n.parent;
                }
            };
            Some(id)
        })
    }

    fn element_name(&self, id: u32) -> Option<&str> {
        match self.node(id).kind {
            Kind::Element(name, _) => Some(self.names.name(name)),
            _ => None,
        }
    }

    fn is_text(&self, id: u32) -> bool {
        id != NIL && matches!(self.node(id).kind, Kind::Text(..))
    }

    /// lxml `len(el)`: children that are not text.
    fn non_text_children(&self, id: u32) -> Vec<u32> {
        let mut out = Vec::new();
        let mut c = self.node(id).first;
        while c != NIL {
            if !self.is_text(c) {
                out.push(c);
            }
            c = self.node(c).next;
        }
        out
    }

    /// The text nodes from `id` on, concatenated (lxml `.text` / `.tail`).
    fn text_run(&self, mut id: u32) -> String {
        let mut out = Vec::new();
        while self.is_text(id) {
            if let Kind::Text(a, b) = self.node(id).kind {
                out.extend_from_slice(&self.text[a as usize..b as usize]);
            }
            id = self.node(id).next;
        }
        String::from_utf8_lossy(&out).into_owned()
    }

    fn children_named(&self, parent: u32, name: &str) -> Vec<u32> {
        let mut out = Vec::new();
        let mut c = self.node(parent).first;
        while c != NIL {
            if self.element_name(c) == Some(name) {
                out.push(c);
            }
            c = self.node(c).next;
        }
        out
    }

    /// Whether the text run from `id` on is XML compatible (lxml `_is_valid_xml_utf8`).
    fn run_valid(&self, mut id: u32) -> bool {
        while self.is_text(id) {
            if let Kind::Text(a, b) = self.node(id).kind
                && !xml_compatible(&self.text[a as usize..b as usize])
            {
                return false;
            }
            id = self.node(id).next;
        }
        true
    }

    fn tail(&self, id: u32) -> Tail {
        match self.node(id).kind {
            Kind::Element(name, _) => self.names.info(name).tail,
            _ => Tail::None,
        }
    }

    /// lxml `drop_tree()`: the tail is appended to the previous sibling's tail or the
    /// parent's text, then the element is removed.
    fn drop_tree(&mut self, id: u32) -> Result<(), Error> {
        let n = *self.node(id);
        if self.is_text(n.next) {
            let mut prev = n.prev;
            while self.is_text(prev) {
                prev = self.node(prev).prev;
            }
            let base = if prev == NIL {
                self.node(n.parent).first
            } else {
                self.node(prev).next
            };
            if !self.run_valid(n.next) || !self.run_valid(base) {
                return Err(Error::NotXmlCompatible);
            }
        }
        self.unlink(id);
        Ok(())
    }

    /// `el.text_content()` after `el.tail = "\n" + el.tail` for blocks and `" "` for cells
    /// (`root_tail` for the root itself), and whether it is the walk's bytes exactly (valid
    /// UTF-8). With `walk`, rule 11a's tables, rows and cells as byte ranges in it.
    fn text_content(
        &self,
        root: u32,
        root_tail: Tail,
        mut walk: Option<&mut Walk>,
    ) -> Result<(String, bool), Error> {
        if root_tail != Tail::None && !self.run_valid(self.node(root).next) {
            return Err(Error::NotXmlCompatible);
        }
        let mut out: Vec<u8> = Vec::with_capacity(self.text.len());
        if let Some(w) = walk.as_deref_mut() {
            w.enter(self.table_part(root), self.span(root), 0, self.fact(root));
        }
        let mut id = self.node(root).first;
        while id != NIL {
            let n = self.node(id);
            match n.kind {
                Kind::Text(a, b) => out.extend_from_slice(&self.text[a as usize..b as usize]),
                Kind::Element(..) => {
                    if let Some(w) = walk.as_deref_mut() {
                        w.enter(self.table_part(id), self.span(id), out.len(), self.fact(id));
                    }
                    if n.first != NIL {
                        id = n.first;
                        continue;
                    }
                }
                _ => {}
            }
            // leave id, then every ancestor it is the last child of, emitting their tails
            let mut cur = id;
            loop {
                let element = matches!(self.node(cur).kind, Kind::Element(..));
                if let Some(w) = walk.as_deref_mut().filter(|_| element) {
                    w.exit(out.len());
                }
                match self.tail(cur) {
                    Tail::None => {}
                    tail => {
                        if !self.run_valid(self.node(cur).next) {
                            return Err(Error::NotXmlCompatible);
                        }
                        out.push(if tail == Tail::Newline { b'\n' } else { b' ' });
                    }
                }
                let next = self.node(cur).next;
                if next != NIL {
                    id = next;
                    break;
                }
                cur = self.node(cur).parent;
                if cur == root || cur == NIL {
                    id = NIL;
                    break;
                }
            }
        }
        if let Some(w) = walk {
            w.exit(out.len());
        }
        Ok(match String::from_utf8(out) {
            Ok(s) => (s, true),
            Err(e) => (String::from_utf8_lossy(e.as_bytes()).into_owned(), false),
        })
    }

    /// Rule 11: what an element is to a table (by rule 1's reading of its name).
    fn table_part(&self, id: u32) -> TablePart {
        match self.node(id).kind {
            Kind::Element(name, _) => self.names.info(name).table_part,
            _ => TablePart::None,
        }
    }

    /// A cell's colspan (rule 11a): 1 unless recorded.
    fn span(&self, id: u32) -> u32 {
        self.spans.get(&id).copied().unwrap_or(1)
    }

    /// What an element is to rule 14.
    fn ix(&self, id: u32) -> Ix {
        match self.node(id).kind {
            Kind::Element(name, _) => self.names.info(name).ix,
            _ => Ix::None,
        }
    }

    /// The node itself when it is a "*:nonfraction" element (rule 14b).
    fn fact(&self, id: u32) -> Option<u32> {
        (self.ix(id) == Ix::Fact).then_some(id)
    }

    /// lxml `el.get(name)` for rule 14's attributes.
    fn attr(&self, id: u32, name: &[u8]) -> Option<String> {
        let (_, v) = self.attrs.get(&id)?.iter().find(|(n, _)| &**n == name)?;
        Some(String::from_utf8_lossy(v).into_owned())
    }

    /// lxml `el.text_content()` before the tails rule 1 adds: the subtree's text nodes.
    fn subtree_text(&self, id: u32) -> String {
        let mut out = Vec::new();
        for d in self.subtree(id) {
            if let Kind::Text(a, b) = self.node(d).kind {
                out.extend_from_slice(&self.text[a as usize..b as usize]);
            }
        }
        String::from_utf8_lossy(&out).into_owned()
    }
}

/// lxml `_is_valid_xml_utf8`: no ASCII control characters but tab, newline and carriage
/// return, no U+FFFE or U+FFFF.
fn xml_compatible(s: &[u8]) -> bool {
    !s.iter()
        .any(|&c| c < 0x20 && !matches!(c, 0x09 | 0x0a | 0x0d))
        && !s
            .windows(3)
            .any(|w| w == [0xef, 0xbf, 0xbe] || w == [0xef, 0xbf, 0xbf])
}

/// lxml.html.defs.block_tags.
const LXML_BLOCK_TAGS: &[&str] = &[
    "address",
    "blockquote",
    "caption",
    "center",
    "col",
    "colgroup",
    "dd",
    "del",
    "dir",
    "div",
    "dl",
    "dt",
    "fieldset",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "ins",
    "isindex",
    "legend",
    "li",
    "menu",
    "noscript",
    "ol",
    "optgroup",
    "option",
    "p",
    "pre",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
];

/// `lxml.html.fromstring`: the element whose text is extracted, and the tail kind of its tag
/// (a body is renamed to div or span).
fn select_root(dom: &mut Dom, full: bool) -> Result<(u32, Tail), Error> {
    let root = {
        let mut c = dom.node(0).first;
        while c != NIL && !matches!(dom.node(c).kind, Kind::Element(..)) {
            c = dom.node(c).next;
        }
        c
    };
    if root == NIL {
        return Err(Error::Empty);
    }
    if full {
        return Ok((root, Tail::None));
    }
    let bodies = dom.children_named(root, "body");
    if let Some((&body, others)) = bodies.split_first() {
        // body.text / body[-1].tail += other.text; body.extend(other); other.drop_tree()
        for &other in others {
            if dom.is_text(dom.node(other).first) {
                let last = dom.non_text_children(body).last().copied();
                let base = last.map_or(dom.node(body).first, |l| dom.node(l).next);
                if !dom.run_valid(base) || !dom.run_valid(dom.node(other).first) {
                    return Err(Error::NotXmlCompatible);
                }
            }
            let mut c = dom.node(other).first;
            while c != NIL {
                let next = dom.node(c).next;
                dom.unlink(c);
                dom.append(body, c);
                c = next;
            }
            dom.drop_tree(other)?;
        }
    }
    let heads = dom.children_named(root, "head");
    if let Some((&head, others)) = heads.split_first() {
        // head.extend(other); other.drop_tree(): the other head's leading text is lost
        for &other in others {
            let mut c = dom.node(other).first;
            while dom.is_text(c) {
                c = dom.node(c).next;
            }
            while c != NIL {
                let next = dom.node(c).next;
                dom.unlink(c);
                dom.append(head, c);
                c = next;
            }
            dom.drop_tree(other)?;
        }
        return Ok((root, Tail::None));
    }
    let Some(&body) = bodies.first() else {
        return Ok((root, Tail::None));
    };
    let children = dom.non_text_children(body);
    if children.len() == 1 {
        let blank = |s: String| py_strip(&s).is_empty();
        let text = blank(dom.text_run(dom.node(body).first));
        let tail = blank(dom.text_run(dom.node(children[0]).next));
        if text && tail {
            return Ok((children[0], dom.tail(children[0])));
        }
    }
    let block = dom.subtree(body).any(|id| {
        dom.element_name(id)
            .is_some_and(|n| LXML_BLOCK_TAGS.contains(&n.rsplit('}').next().unwrap_or("")))
    });
    Ok((body, if block { Tail::Newline } else { Tail::None }))
}

/// `doc.xpath(_DROP)` then `drop_tree()` on each match, in document order.
fn drop_hidden(dom: &mut Dom) -> Result<(), Error> {
    let matches: Vec<u32> = dom
        .subtree(0)
        .filter(|&id| matches!(dom.node(id).kind, Kind::Element(_, true)))
        .collect();
    for id in matches {
        if dom.node(id).parent == 0 {
            return Err(Error::DropRoot);
        }
        dom.drop_tree(id)?;
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Element names

#[derive(Default)]
struct Fnv(u64);

impl Hasher for Fnv {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        let mut h = if self.0 == 0 {
            0xcbf2_9ce4_8422_2325
        } else {
            self.0
        };
        for b in bytes {
            h = (h ^ u64::from(*b)).wrapping_mul(0x100_0000_01b3);
        }
        self.0 = h;
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Tail {
    None,
    Newline,
    Space,
}

struct NameInfo {
    name: Box<str>,
    empty: bool,
    mode: u8,
    priority: u8,
    tail: Tail,
    drop: bool,
    table_part: TablePart,
    ix: Ix,
}

struct Names {
    ids: HashMap<Box<[u8]>, u32, BuildHasherDefault<Fnv>>,
    infos: Vec<NameInfo>,
    start_close: HashMap<(u32, u32), bool, BuildHasherDefault<Fnv>>,
}

const HTML: u32 = 0;
const HEAD: u32 = 1;
const BODY: u32 = 2;
const P: u32 = 3;
const META: u32 = 4;

impl Names {
    fn new() -> Self {
        let mut names = Names {
            ids: HashMap::default(),
            infos: Vec::new(),
            start_close: HashMap::default(),
        };
        for n in ["html", "head", "body", "p", "meta"] {
            names.intern(n.as_bytes());
        }
        names
    }

    fn intern(&mut self, name: &[u8]) -> u32 {
        if let Some(&id) = self.ids.get(name) {
            return id;
        }
        let id = self.infos.len() as u32;
        let s: Box<str> = String::from_utf8_lossy(name).into();
        // lxml: el.tag.rsplit("}", 1)[-1].lower()
        let local = s.rsplit('}').next().unwrap_or("").to_lowercase();
        let tail = if BLOCK.contains(&local.as_str()) {
            Tail::Newline
        } else if CELL.contains(&local.as_str()) {
            Tail::Space
        } else {
            Tail::None
        };
        let table_part = match local.as_str() {
            "table" => TablePart::Table,
            "thead" => TablePart::Thead,
            "tr" => TablePart::Tr,
            "td" => TablePart::Td,
            "th" => TablePart::Th,
            _ => TablePart::None,
        };
        let info = NameInfo {
            empty: tables::EMPTY_ELEMENTS.contains(&&*s),
            mode: tables::DATA_ELEMENTS
                .iter()
                .find(|(n, _)| *n == &*s)
                .map_or(MODE_DATA, |(_, m)| *m as u8),
            priority: tables::END_PRIORITY
                .iter()
                .find(|(n, _)| *n == &*s)
                .map_or(100, |(_, p)| *p),
            tail,
            drop: DROP.contains(&&*s),
            table_part,
            ix: Ix::of(&local),
            name: s,
        };
        self.infos.push(info);
        self.ids.insert(name.into(), id);
        id
    }

    fn info(&self, id: u32) -> &NameInfo {
        &self.infos[id as usize]
    }

    fn name(&self, id: u32) -> &str {
        &self.infos[id as usize].name
    }

    /// htmlCheckAutoClose: does a `new` start tag close an open `old` element?
    fn start_closes(&mut self, new: u32, old: u32) -> bool {
        if let Some(&closes) = self.start_close.get(&(old, new)) {
            return closes;
        }
        let key = (self.name(old), self.name(new));
        let closes = tables::START_CLOSE.binary_search(&key).is_ok();
        self.start_close.insert((old, new), closes);
        closes
    }
}

// ---------------------------------------------------------------------------------------------
// Tree builder: htmlnamePush/Pop, htmlAutoClose*, htmlCheckImplied and the SAX2 callbacks

struct Tree {
    dom: Dom,
    /// (name, node) of open elements; ctxt->nameTab and ctxt->nodeTab.
    stack: Vec<(u32, u32)>,
    insert_mode: u8,
    /// Misplaced html/head/body start tags not yet matched by an end tag (ctxt->depth).
    depth: u32,
    stopped: bool,
    /// Length of the text node being appended to, when coalescing is checked (nodelen).
    text_len: Option<usize>,
}

impl Tree {
    fn current(&self) -> Option<u32> {
        self.stack.last().map(|&(name, _)| name)
    }

    fn current_node(&self) -> u32 {
        self.stack.last().map_or(0, |&(_, node)| node)
    }

    fn open(&self, name: u32) -> bool {
        self.stack.iter().any(|&(n, _)| n == name)
    }

    /// htmlnamePush + xmlSAX2StartHtmlElement.
    /// The element's node, when it was added to the tree.
    fn push(&mut self, name: u32, hidden: bool) -> Option<u32> {
        if name == HEAD && self.insert_mode < INSERT_IN_HEAD {
            self.insert_mode = INSERT_IN_HEAD;
        }
        if name == BODY && self.insert_mode < INSERT_IN_BODY {
            self.insert_mode = INSERT_IN_BODY;
        }
        if self.stopped {
            return None;
        }
        let drop = hidden || self.dom.names.info(name).drop;
        let node = self.dom.create(Kind::Element(name, drop));
        let parent = self.current_node();
        self.dom.append(parent, node);
        self.text_len = None;
        if self.stack.len() >= MAX_DEPTH {
            self.dom.unlink(node);
            self.stopped = true;
            return None;
        }
        self.stack.push((name, node));
        Some(node)
    }

    /// xmlSAX2EndElement + htmlnamePop.
    fn pop(&mut self) {
        self.text_len = None;
        self.stack.pop();
    }

    /// htmlAutoClose.
    fn auto_close(&mut self, new: u32) {
        while let Some(cur) = self.current() {
            if !self.dom.names.start_closes(new, cur) {
                break;
            }
            self.pop();
        }
    }

    /// htmlAutoCloseOnClose.
    fn auto_close_on_close(&mut self, name: u32) {
        let priority = self.dom.names.info(name).priority;
        for &(open, _) in self.stack.iter().rev() {
            if open == name {
                break;
            }
            if self.dom.names.info(open).priority > priority {
                return;
            }
        }
        while self.current().is_some_and(|cur| cur != name) {
            self.pop();
        }
    }

    /// htmlCheckImplied.
    fn check_implied(&mut self, new: u32) {
        if new == HTML {
            return;
        }
        if self.stack.is_empty() {
            self.push(HTML, false);
        }
        if new == BODY || new == HEAD {
            return;
        }
        let name = self.dom.names.name(new);
        if self.stack.len() <= 1
            && matches!(
                name,
                "script" | "style" | "meta" | "link" | "title" | "base"
            )
        {
            if self.insert_mode >= INSERT_IN_HEAD {
                return;
            }
            self.push(HEAD, false);
        } else if !matches!(name, "noframes" | "frame" | "frameset") {
            if self.insert_mode >= INSERT_IN_BODY {
                return;
            }
            if self.stack.iter().any(|&(n, _)| n == BODY || n == HEAD) {
                return;
            }
            self.push(BODY, false);
        }
    }

    /// htmlStartCharData.
    fn start_char_data(&mut self) {
        if self.current() == Some(HEAD) {
            self.auto_close(P);
        }
        self.check_implied(P);
    }

    /// htmlCharDataSAXCallback.
    fn char_data(&mut self, mut buf: &[u8], mode: u8) {
        if self.stopped {
            return;
        }
        if (mode == MODE_DATA || mode == MODE_RCDATA)
            && matches!(self.current(), None | Some(HTML) | Some(HEAD))
        {
            let ws = buf.iter().take_while(|c| is_ws(**c)).count();
            if ws > 0 {
                self.characters(&buf[..ws]);
                buf = &buf[ws..];
            }
            if buf.is_empty() {
                return;
            }
            self.start_char_data();
            if self.stopped {
                return;
            }
        }
        self.characters(buf);
    }

    /// xmlSAX2Characters and xmlSAX2CDataBlock: lxml reads CDATA as text, so both coalesce
    /// into one node.
    fn characters(&mut self, buf: &[u8]) {
        if self.stopped || self.stack.is_empty() {
            return;
        }
        let parent = self.current_node();
        let last = self.dom.node(parent).last;
        let end = self.dom.text.len() as u32;
        let coalesce =
            last != NIL && matches!(self.dom.node(last).kind, Kind::Text(_, e) if e == end);
        if coalesce {
            let Kind::Text(a, _) = self.dom.node(last).kind else {
                unreachable!()
            };
            if let Some(len) = self.text_len
                && (buf.len() > MAX_TEXT || len > MAX_TEXT - buf.len())
            {
                self.stopped = true;
                return;
            }
            self.dom.text.extend_from_slice(buf);
            let end = self.dom.text.len() as u32;
            self.dom.nodes[last as usize].kind = Kind::Text(a, end);
            self.text_len = Some((end - a) as usize);
        } else {
            self.dom.text.extend_from_slice(buf);
            let node = self.dom.create(Kind::Text(end, self.dom.text.len() as u32));
            self.dom.append(parent, node);
            self.text_len = Some(buf.len());
        }
    }

    /// xmlSAX2Comment.
    fn comment(&mut self) {
        if self.stopped {
            return;
        }
        let node = self.dom.create(Kind::Comment);
        let parent = self.current_node();
        self.dom.append(parent, node);
        self.text_len = None;
    }
}

// ---------------------------------------------------------------------------------------------
// Parser: htmlParseDocument and the tokenizer

fn is_ws(c: u8) -> bool {
    c == b' ' || (0x09..=0x0d).contains(&c) && c != 0x0b
}

fn is_letter(c: u8) -> bool {
    c.is_ascii_alphabetic()
}

/// htmlAsciiMask: bytes that end htmlParseData.
#[derive(Clone, Copy)]
enum Mask {
    Dq,
    Sq,
    Gt,
    Dash,
    WsGt,
    DqGt,
    SqGt,
}

impl Mask {
    fn matches(self, c: u8) -> bool {
        match self {
            Mask::Dq => c == b'"',
            Mask::Sq => c == b'\'',
            Mask::Gt => c == b'>',
            Mask::Dash => c == b'-',
            Mask::WsGt => matches!(c, 0x09 | 0x0a | 0x0c | 0x0d | b' ' | b'>'),
            Mask::DqGt => c == b'"' || c == b'>',
            Mask::SqGt => c == b'\'' || c == b'>',
        }
    }

    /// Bytes htmlParseData must look at: the mask, references, NUL, CR and non-ASCII.
    fn stops(self) -> &'static [bool; 256] {
        const fn table(mask: &[u8]) -> [bool; 256] {
            let mut t = [false; 256];
            let mut i = 0;
            while i < mask.len() {
                t[mask[i] as usize] = true;
                i += 1;
            }
            t[0] = true;
            t[b'&' as usize] = true;
            t[b'\r' as usize] = true;
            let mut c = 0x80;
            while c < 256 {
                t[c] = true;
                c += 1;
            }
            t
        }
        static DQ: [bool; 256] = table(b"\"");
        static SQ: [bool; 256] = table(b"'");
        static GT: [bool; 256] = table(b">");
        static DASH: [bool; 256] = table(b"-");
        static WS_GT: [bool; 256] = table(b"\t\n\x0c\r >");
        static DQ_GT: [bool; 256] = table(b"\">");
        static SQ_GT: [bool; 256] = table(b"'>");
        match self {
            Mask::Dq => &DQ,
            Mask::Sq => &SQ,
            Mask::Gt => &GT,
            Mask::Dash => &DASH,
            Mask::WsGt => &WS_GT,
            Mask::DqGt => &DQ_GT,
            Mask::SqGt => &SQ_GT,
        }
    }
}

/// Bytes htmlParseCharData's accelerator stops at: NUL, \n, \r, &, -, < and non-ASCII.
static SPECIAL: [bool; 256] = {
    let mut t = [false; 256];
    t[0] = true;
    t[b'\n' as usize] = true;
    t[b'\r' as usize] = true;
    t[b'&' as usize] = true;
    t[b'-' as usize] = true;
    t[b'<' as usize] = true;
    let mut i = 0x80;
    while i < 256 {
        t[i] = true;
        i += 1;
    }
    t
};

/// htmlC1Remap: numeric references to C1 controls are read as windows-1252.
const C1_REMAP: [u32; 32] = [
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
    0x0152, 0x008D, 0x017D, 0x008F, 0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
];

/// htmlCodePointToUtf8.
fn code_point(cp: u32) -> ([u8; 4], usize) {
    let c = if (0x80..0xa0).contains(&cp) {
        C1_REMAP[(cp - 0x80) as usize]
    } else if cp == 0 || (0xd800..0xe000).contains(&cp) || cp > 0x10ffff {
        0xfffd
    } else {
        cp
    };
    let ch = char::from_u32(c).unwrap_or('\u{FFFD}');
    let mut out = [0; 4];
    let len = ch.encode_utf8(&mut out).len();
    (out, len)
}

/// htmlValidateUtf8 (not partial): the sequence length, or 0 when invalid.
fn utf8_len(s: &[u8]) -> usize {
    let c = s[0];
    let at = |i: usize| s.get(i).copied();
    if c < 0xc2 {
        0
    } else if c < 0xe0 {
        match at(1) {
            Some(b) if b & 0xc0 == 0x80 => 2,
            _ => 0,
        }
    } else if c < 0xf0 {
        let (Some(b1), Some(b2)) = (at(1), at(2)) else {
            return 0;
        };
        let v = u32::from(c) << 16 | u32::from(b1) << 8 | u32::from(b2);
        if v & 0x00c0c0 != 0x008080 || v & 0x0f2000 == 0 || v & 0x0f2000 == 0x0d2000 {
            0
        } else {
            3
        }
    } else {
        let (Some(b1), Some(b2), Some(b3)) = (at(1), at(2), at(3)) else {
            return 0;
        };
        let v = u32::from(c) << 24 | u32::from(b1) << 16 | u32::from(b2) << 8 | u32::from(b3);
        if v & 0x00c0_c0c0 != 0x0080_8080 || !(0xf090_0000..0xf490_0000).contains(&v) {
            0
        } else {
            4
        }
    }
}

/// htmlFindEntityPrefix: the longest named reference at the start of `s` (after '&').
/// Returns (bytes consumed, replacement).
fn entity_prefix(s: &[u8], attr: bool) -> Option<(usize, &'static str)> {
    if s.len() < 2 || !is_letter(s[0]) {
        return None;
    }
    let run = s
        .iter()
        .take(32)
        .take_while(|c| c.is_ascii_alphanumeric())
        .count();
    let lookup = |name: &[u8]| {
        tables::ENTITIES
            .binary_search_by(|(k, _)| k.as_bytes().cmp(name))
            .ok()
            .map(|i| tables::ENTITIES[i].1)
    };
    let mut name = [0u8; 33];
    for len in (1..=run).rev() {
        let term = s.get(len).copied().unwrap_or(0);
        if term == b';' {
            name[..len].copy_from_slice(&s[..len]);
            name[len] = b';';
            if let Some(v) = lookup(&name[..=len]) {
                return Some((len + 1, v));
            }
        }
        if (!attr || (!term.is_ascii_alphanumeric() && term != b'='))
            && let Some(v) = lookup(&s[..len])
        {
            return Some((len, v));
        }
    }
    None
}

enum Encoding {
    Utf8,
    Latin1,
    Ascii,
    Windows1252,
    Other(&'static encoding_rs::Encoding),
}

/// xmlCreateCharEncodingHandler for the names a document may declare: libxml2's own
/// converters, then the libiconv names lxml's build resolves. Legacy multibyte and Mac
/// encodings go through encoding_rs, whose tables may differ from libiconv's.
fn encoding_for(name: &[u8]) -> Option<Encoding> {
    let name = String::from_utf8_lossy(name).to_ascii_uppercase();
    match name.as_str() {
        "UTF-8" | "UTF8" => Some(Encoding::Utf8),
        "ISO-8859-1" | "ISO_8859-1" | "ISO8859-1" | "ISO-LATIN-1" | "ISO LATIN 1" | "LATIN1"
        | "L1" | "CP819" | "IBM819" | "ISO-IR-100" | "CSISOLATIN1" | "ISO_8859-1:1987" => {
            Some(Encoding::Latin1)
        }
        "ASCII" | "US-ASCII" | "US" | "ANSI_X3.4-1968" | "ISO646-US" | "CSASCII" => {
            Some(Encoding::Ascii)
        }
        "WINDOWS-1252" | "CP1252" | "MS-ANSI" => Some(Encoding::Windows1252),
        "UTF-16" | "UTF16" | "UTF-16LE" => Some(Encoding::Other(encoding_rs::UTF_16LE)),
        "UTF-16BE" => Some(Encoding::Other(encoding_rs::UTF_16BE)),
        _ => encoding_rs::Encoding::for_label_no_replacement(name.as_bytes()).map(Encoding::Other),
    }
}

/// Decode to UTF-8 up to the first byte the converter rejects: libxml2 stops reading there.
fn decode(enc: Encoding, bytes: &[u8]) -> Vec<u8> {
    let mut out = String::new();
    match enc {
        Encoding::Utf8 => return bytes.to_vec(),
        Encoding::Latin1 => out.extend(bytes.iter().map(|&b| b as char)),
        Encoding::Ascii => out.extend(
            bytes
                .iter()
                .take_while(|b| b.is_ascii())
                .map(|&b| b as char),
        ),
        Encoding::Windows1252 => {
            // libiconv leaves these five bytes undefined; encoding_rs maps them to C1 controls
            let end = bytes
                .iter()
                .position(|b| matches!(b, 0x81 | 0x8d | 0x8f | 0x90 | 0x9d))
                .unwrap_or(bytes.len());
            return decode(Encoding::Other(encoding_rs::WINDOWS_1252), &bytes[..end]);
        }
        Encoding::Other(e) => {
            let mut decoder = e.new_decoder_without_bom_handling();
            let max = decoder
                .max_utf8_buffer_length_without_replacement(bytes.len())
                .unwrap_or(bytes.len() * 3 + 16);
            out.reserve(max);
            // stops at the first malformed sequence
            let _ = decoder.decode_to_string_without_replacement(bytes, &mut out, true);
        }
    }
    out.into_bytes()
}

/// INPUT_CHUNK: GROW reads more when fewer bytes than this are buffered ahead.
const INPUT_CHUNK: usize = 250;
/// MINLEN: memory input is read this many bytes at a time.
const READ_SIZE: usize = 4000;
/// LINE_LEN: SHRINK keeps this many bytes before the input position.
const LINE_LEN: usize = 80;
/// XML_MAX_LOOKUP_LIMIT: xmlParserGrow halts when one token spans more of the buffer.
const MAX_LOOKUP: usize = 10_000_000;

struct Parser<'a> {
    buf: Cow<'a, [u8]>,
    /// ctxt->input->cur.
    pos: usize,
    /// ctxt->input->base and ctxt->input->end: htmlCtxtReadMemory streams the document
    /// through a read callback, and encoding sniffing and the lookup limit see only this window.
    base: usize,
    end: usize,
    /// XML_INPUT_HAS_ENCODING: until set, the first non-ASCII byte picks an encoding.
    has_encoding: bool,
    /// ctxt->endCheckState.
    end_check: u8,
    tree: Tree,
    name: Vec<u8>,
}

impl<'a> Parser<'a> {
    fn new(raw: &'a [u8]) -> Self {
        Parser {
            buf: Cow::Borrowed(raw),
            pos: 0,
            base: 0,
            end: 0,
            has_encoding: false,
            end_check: MODE_DATA,
            tree: Tree {
                dom: Dom::new(),
                stack: Vec::new(),
                insert_mode: 1,
                depth: 0,
                stopped: false,
                text_len: None,
            },
            name: Vec::with_capacity(MAX_NAME),
        }
    }

    /// CUR / NXT(i): the buffer reads as NUL past its end.
    fn peek(&self, i: usize) -> u8 {
        let at = self.pos + i;
        if at < self.end { self.buf[at] } else { 0 }
    }

    fn at_end(&self) -> bool {
        self.pos >= self.end
    }

    /// xmlParserGrow with the input at `cur`.
    fn grow_at(&mut self, cur: usize) {
        if cur - self.base > MAX_LOOKUP {
            self.tree.stopped = true;
            return;
        }
        if self.end - cur >= INPUT_CHUNK {
            return;
        }
        self.end = (self.end + READ_SIZE).min(self.buf.len());
    }

    /// GROW.
    fn grow(&mut self) {
        if self.end - self.pos < INPUT_CHUNK {
            self.grow_at(self.pos);
        }
    }

    /// SHRINK with the input at `cur` (xmlParserShrink).
    fn shrink_at(&mut self, cur: usize) {
        if cur - self.base > 2 * INPUT_CHUNK && self.end - cur < 2 * INPUT_CHUNK {
            self.base = cur - LINE_LEN;
        }
    }

    /// htmlSkipBlankChars.
    fn skip_blanks(&mut self) {
        let mut skipped = 0;
        while !self.tree.stopped {
            if self.pos >= self.end {
                self.grow();
                if self.pos >= self.end {
                    break;
                }
            }
            if !is_ws(self.buf[self.pos]) {
                break;
            }
            self.pos += 1;
            skipped += 1;
        }
        if skipped > 8 {
            self.grow();
        }
    }

    fn upper_is(&self, at: usize, word: &[u8]) -> bool {
        word.iter()
            .enumerate()
            .all(|(i, c)| self.peek(at + i).to_ascii_uppercase() == *c)
    }

    /// Decode the rest of the input to UTF-8 (xmlInputSetEncodingHandler). The buffer window
    /// then covers the whole document: libxml2 reads converted input in other increments.
    fn switch_encoding(&mut self, enc: Encoding) {
        self.has_encoding = true;
        if matches!(enc, Encoding::Utf8) {
            return;
        }
        let decoded = decode(enc, &self.buf[self.pos..]);
        let mut buf = self.buf[..self.pos].to_vec();
        buf.extend_from_slice(&decoded);
        self.buf = Cow::Owned(buf);
        self.end = self.buf.len();
    }

    /// xmlDetectEncoding plus htmlParseDocument's "<?xm" rule.
    fn detect_encoding(&mut self) {
        self.grow_at(0);
        if self.end >= 4 {
            let b = &self.buf[..];
            match (b[0], b[1], b[2], b[3]) {
                (0xef, 0xbb, 0xbf, _) => {
                    self.pos = 3;
                    self.has_encoding = true;
                }
                (0xfe, 0xff, _, _) => {
                    self.pos = 2;
                    self.switch_encoding(Encoding::Other(encoding_rs::UTF_16BE));
                }
                (0xff, 0xfe, _, _) => {
                    self.pos = 2;
                    self.switch_encoding(Encoding::Other(encoding_rs::UTF_16LE));
                }
                (0x00, 0x3c, 0x00, 0x3f) => {
                    self.switch_encoding(Encoding::Other(encoding_rs::UTF_16BE))
                }
                (0x3c, 0x00, 0x3f, 0x00) => {
                    self.switch_encoding(Encoding::Other(encoding_rs::UTF_16LE))
                }
                _ => {}
            }
        }
        if !self.has_encoding && self.buf[self.pos..self.end].starts_with(b"<?xm") {
            self.has_encoding = true;
        }
    }

    /// The first non-ASCII byte without a known encoding: htmlFindEncoding (searching the
    /// buffered input up to a NUL), else ISO-8859-1.
    fn guess_encoding(&mut self) {
        let rest = &self.buf[self.pos..self.end];
        let rest = &rest[..rest.iter().position(|&c| c == 0).unwrap_or(rest.len())];
        let guess = find_ignore_case(rest, b"HTTP-EQUIV")
            .and_then(|i| find_ignore_case(&rest[i..], b"CONTENT").map(|j| i + j))
            .and_then(|i| find_ignore_case(&rest[i..], b"CHARSET=").map(|j| i + j + 8))
            .map(|i| {
                let n = rest[i..]
                    .iter()
                    .take_while(|c| {
                        c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b':' | b'/')
                    })
                    .count();
                &rest[i..i + n]
            })
            .filter(|name| !name.is_empty())
            .map(|name| name.to_vec());
        match guess {
            None => self.switch_encoding(Encoding::Latin1),
            Some(name) => match encoding_for(&name) {
                Some(enc) => self.switch_encoding(enc),
                None => self.has_encoding = true,
            },
        }
    }

    /// htmlParseDocument.
    fn parse(mut self) -> Dom {
        self.detect_encoding();
        self.skip_blanks();
        self.prolog_comments();
        if self.peek(0) == b'<' && self.peek(1) == b'!' && self.upper_is(2, b"DOCTYPE") {
            self.doctype();
        }
        self.skip_blanks();
        self.prolog_comments();
        self.content();
        self.tree.dom
    }

    fn prolog_comments(&mut self) {
        while self.peek(0) == b'<' {
            if self.peek(1) == b'!' && self.peek(2) == b'-' && self.peek(3) == b'-' {
                self.pos += 4;
                self.comment(false);
            } else if self.peek(1) == b'?' {
                self.pos += 1;
                self.comment(true);
            } else {
                break;
            }
            self.skip_blanks();
        }
    }

    /// htmlParseContent.
    fn content(&mut self) {
        self.grow();
        while !self.tree.stopped && !self.at_end() {
            if self.end_check == MODE_DATA && self.peek(0) == b'<' {
                let next = self.peek(1);
                if next == b'/' {
                    self.end_tag();
                } else if next == b'!' {
                    if self.upper_is(2, b"DOCTYPE") {
                        self.doctype();
                    } else if self.peek(2) == b'-' && self.peek(3) == b'-' {
                        self.pos += 4;
                        self.comment(false);
                    } else {
                        self.pos += 2;
                        self.comment(true);
                    }
                } else if next == b'?' {
                    self.pos += 1;
                    self.comment(true);
                } else if is_letter(next) {
                    self.element();
                } else {
                    self.tree.start_char_data();
                    self.tree.characters(b"<");
                    self.pos += 1;
                }
            } else {
                self.char_data();
            }
            self.shrink_at(self.pos);
            self.grow();
        }
        if self.at_end() {
            while !self.tree.stack.is_empty() {
                self.tree.pop();
            }
        }
    }

    /// htmlParseHTMLName: lowercased into `self.name`.
    fn html_name(&mut self, attr: bool) {
        self.name.clear();
        let stop = if attr { b'=' } else { b' ' };
        let mut n = 0;
        let mut i = self.pos;
        let mut eof = false;
        loop {
            if !eof && self.end - i < 32 {
                let avail = self.end - i;
                self.pos = i;
                self.shrink_at(i);
                self.grow_at(i);
                eof = self.end - i == avail;
            }
            if i >= self.end {
                break;
            }
            let c = self.buf[i];
            if n != 0 && (c == b'/' || c == b'>' || c == stop || is_ws(c)) {
                break;
            }
            let mut size = 1;
            if c == 0 {
                if n + 3 <= MAX_NAME {
                    self.name.extend_from_slice(FFFD);
                    n += 3;
                }
            } else if c < 0x80 {
                if n < MAX_NAME {
                    self.name.push(c.to_ascii_lowercase());
                    n += 1;
                }
            } else {
                let len = utf8_len(&self.buf[i..self.end]);
                if len > 0 {
                    size = len;
                    if n + len <= MAX_NAME {
                        self.name.extend_from_slice(&self.buf[i..i + len]);
                        n += len;
                    }
                } else if n + 3 <= MAX_NAME {
                    self.name.extend_from_slice(FFFD);
                    n += 3;
                }
            }
            i += size;
        }
        self.pos = i;
        self.shrink_at(i);
    }

    /// htmlParseData: attribute values, comments and doctype parts. The value is appended to
    /// `out` when given.
    fn data(&mut self, mask: Mask, comment: bool, refs: bool, mut out: Option<&mut Vec<u8>>) {
        let stops = mask.stops();
        let mut term_skip: Option<usize> = None;
        let mut eof = false;
        while !self.tree.stopped {
            let chunk = self.pos;
            let mut i = chunk;
            let mut skip = 0;
            let mut repl: &[u8] = b"";
            let mut ncr = 0u32;
            let mut ncr_size = 0;
            let mut cp = 0u32;
            let mut restart = false;
            while !self.tree.stopped {
                if !eof && self.end - i <= 64 {
                    let avail = self.end - i;
                    self.grow_at(i);
                    eof = self.end - i == avail;
                }
                if i >= self.end {
                    term_skip = Some(0);
                    break;
                }
                if ncr == 0 {
                    let limit = if eof { self.end } else { self.end - 64 };
                    while i < limit && !stops[self.buf[i] as usize] {
                        i += 1;
                    }
                    if i == limit {
                        continue;
                    }
                }
                let c = self.buf[i];
                let mut size = 1;
                if mask.matches(c) {
                    if comment {
                        let at = |k: usize| self.buf[..self.end].get(i + k).copied();
                        match (at(1), at(2), at(3)) {
                            (None, _, _) => term_skip = Some(1),
                            (Some(b'-'), None, _) => term_skip = Some(2),
                            (Some(b'-'), Some(b'>'), _) => term_skip = Some(3),
                            (Some(b'-'), Some(b'!'), None) => term_skip = Some(3),
                            (Some(b'-'), Some(b'!'), Some(b'>')) => term_skip = Some(4),
                            _ => {}
                        }
                        if term_skip.is_some() {
                            break;
                        }
                    } else {
                        term_skip = Some(0);
                        break;
                    }
                }
                if ncr != 0 {
                    let lc = c | 0x20;
                    let digit = if c.is_ascii_digit() {
                        u32::from(c - b'0')
                    } else if ncr == 16 && (b'a'..=b'f').contains(&lc) {
                        u32::from(lc - b'a' + 10)
                    } else {
                        if c == b';' {
                            i += 1;
                            ncr_size += 1;
                        }
                        break;
                    };
                    cp = (cp * ncr + digit).min(0x110000);
                    ncr_size += 1;
                    i += size;
                    continue;
                }
                match c {
                    b'&' if refs => {
                        let at = |k: usize| self.buf[..self.end].get(i + k).copied();
                        if at(1) == Some(b'#') {
                            match at(2) {
                                Some(x) if x | 0x20 == b'x' => {
                                    if at(3).is_some_and(|d| d.is_ascii_hexdigit()) {
                                        ncr = 16;
                                        size = 3;
                                        ncr_size = 3;
                                        cp = 0;
                                    }
                                }
                                Some(d) if d.is_ascii_digit() => {
                                    ncr = 10;
                                    size = 2;
                                    ncr_size = 2;
                                    cp = 0;
                                }
                                _ => {}
                            }
                        } else if let Some((n, v)) = entity_prefix(&self.buf[i + 1..self.end], true)
                        {
                            skip = n + 1;
                            repl = v.as_bytes();
                            break;
                        }
                    }
                    0 => {
                        skip = 1;
                        repl = FFFD;
                        break;
                    }
                    b'\r' => {
                        skip = 1;
                        if self.peek_at(i + 1) != b'\n' {
                            repl = b"\n";
                        }
                        break;
                    }
                    c if c >= 0x80 => {
                        if !self.has_encoding {
                            if i == chunk {
                                restart = true;
                            }
                            break;
                        }
                        size = utf8_len(&self.buf[i..self.end]);
                        if size == 0 {
                            skip = 1;
                            repl = FFFD;
                            break;
                        }
                    }
                    _ => {}
                }
                i += size;
            }
            if restart {
                self.guess_encoding();
                eof = false;
                continue;
            }
            let cpbuf: ([u8; 4], usize);
            if ncr_size > 0 {
                skip = ncr_size;
                i -= ncr_size;
                cpbuf = code_point(cp);
                repl = &cpbuf.0[..cpbuf.1];
            }
            if let Some(out) = out.as_deref_mut() {
                out.extend_from_slice(&self.buf[chunk..i]);
                out.extend_from_slice(repl);
            }
            self.pos = i + skip;
            self.shrink_at(self.pos);
            if term_skip.is_some() {
                break;
            }
        }
        if let Some(n) = term_skip {
            self.pos += n;
        }
    }

    /// The byte at `at`, NUL past the buffered input.
    fn peek_at(&self, at: usize) -> u8 {
        if at < self.end { self.buf[at] } else { 0 }
    }

    /// htmlParseComment.
    fn comment(&mut self, bogus: bool) {
        if bogus {
            self.data(Mask::Gt, false, false, None);
            if self.peek(0) == b'>' {
                self.pos += 1;
            }
        } else if self.peek(0) == b'>' {
            self.pos += 1;
        } else if self.peek(0) == b'-' && self.peek(1) == b'>' {
            self.pos += 2;
        } else {
            self.data(Mask::Dash, true, false, None);
        }
        self.tree.comment();
    }

    /// htmlParseDocTypeDecl: only its extent matters.
    fn doctype(&mut self) {
        self.pos += 9;
        self.skip_blanks();
        if !self.at_end() && self.peek(0) != b'>' {
            self.data(Mask::WsGt, false, false, None);
            self.skip_blanks();
        }
        if self.upper_is(0, b"PUBLIC") {
            self.pos += 6;
            self.skip_blanks();
            if self.doctype_literal() {
                self.skip_blanks();
                self.doctype_literal();
            }
        } else if self.upper_is(0, b"SYSTEM") {
            self.pos += 6;
            self.skip_blanks();
            self.doctype_literal();
        }
        self.skip_bogus_doctype();
    }

    /// htmlSkipBogusDoctype. Its SHRINK sees the input position of the last grow.
    fn skip_bogus_doctype(&mut self) {
        let mut i = self.pos;
        let mut eof = false;
        while !self.tree.stopped {
            if !eof && self.end - i <= 64 {
                let avail = self.end - i;
                self.pos = i;
                self.grow_at(i);
                eof = self.end - i == avail;
            }
            if i >= self.end {
                break;
            }
            i += 1;
            if self.buf[i - 1] == b'>' {
                break;
            }
            self.shrink_at(self.pos);
        }
        self.pos = i;
    }

    fn doctype_literal(&mut self) -> bool {
        let (mask, quote) = match self.peek(0) {
            b'"' => (Mask::DqGt, b'"'),
            b'\'' => (Mask::SqGt, b'\''),
            _ => return false,
        };
        self.pos += 1;
        self.data(mask, false, false, None);
        if self.peek(0) == quote {
            self.pos += 1;
        }
        true
    }

    /// htmlParseAttValue.
    fn att_value(&mut self, out: Option<&mut Vec<u8>>) {
        match self.peek(0) {
            b'"' => {
                self.pos += 1;
                self.data(Mask::Dq, false, true, out);
                if self.peek(0) == b'"' {
                    self.pos += 1;
                }
            }
            b'\'' => {
                self.pos += 1;
                self.data(Mask::Sq, false, true, out);
                if self.peek(0) == b'\'' {
                    self.pos += 1;
                }
            }
            _ => self.data(Mask::WsGt, false, true, out),
        }
    }

    /// Attributes up to '>' or "/>": (name, value) pairs when `keep`, else just the first
    /// style attribute.
    fn attributes(&mut self, keep: bool) -> Vec<(Vec<u8>, Option<Vec<u8>>)> {
        let mut atts: Vec<(Vec<u8>, Option<Vec<u8>>)> = Vec::new();
        self.skip_blanks();
        while !self.at_end()
            && self.peek(0) != b'>'
            && !(self.peek(0) == b'/' && self.peek(1) == b'>')
            && !self.tree.stopped
        {
            if self.peek(0) == b'/' {
                self.pos += 1;
                self.skip_blanks();
                continue;
            }
            self.grow();
            self.html_name(true);
            let wanted = keep && !atts.iter().any(|(n, _)| *n == self.name)
                || !keep && atts.is_empty() && self.name == b"style";
            let name = if wanted {
                std::mem::take(&mut self.name)
            } else {
                Vec::new()
            };
            self.skip_blanks();
            let mut value = None;
            if self.peek(0) == b'=' {
                self.pos += 1;
                self.skip_blanks();
                if wanted {
                    let mut v = Vec::new();
                    self.att_value(Some(&mut v));
                    value = Some(v);
                } else {
                    self.att_value(None);
                }
            }
            if wanted {
                atts.push((name, value));
            }
            self.skip_blanks();
        }
        atts
    }

    /// htmlParseStartTag; returns whether the element was opened.
    fn start_tag(&mut self) -> bool {
        self.end_check = MODE_DATA;
        self.pos += 1;
        self.grow();
        self.html_name(false);
        let name = self.tree.dom.names.intern(&self.name);
        self.tree.auto_close(name);
        self.tree.check_implied(name);
        let mut discard = false;
        if name == HTML && !self.tree.stack.is_empty() {
            discard = true;
            self.tree.depth += 1;
        }
        if name == HEAD && self.tree.stack.len() != 1 {
            discard = true;
            self.tree.depth += 1;
        }
        if name == BODY {
            for _ in self.tree.stack.iter().filter(|&&(n, _)| n == BODY) {
                discard = true;
                self.tree.depth += 1;
            }
        }
        let meta = name == META;
        let cell = matches!(
            self.tree.dom.names.info(name).table_part,
            TablePart::Td | TablePart::Th
        );
        let ix = self.tree.dom.names.info(name).ix.keeps_attributes();
        let atts = self.attributes(meta || cell || ix);
        if self.at_end() {
            return false;
        }
        if meta {
            self.check_meta(&atts);
        }
        if discard {
            return false;
        }
        let hidden = atts
            .iter()
            .find(|(n, _)| n == b"style")
            .is_some_and(|(_, v)| {
                v.as_deref().is_some_and(|v| {
                    let v: Vec<u8> = v.iter().copied().filter(|&c| c != b' ').collect();
                    v.windows(12).any(|w| w == b"display:none")
                })
            });
        let node = self.tree.push(name, hidden);
        if let Some(node) = node.filter(|_| cell) {
            let value = atts
                .iter()
                .find(|(n, _)| n == b"colspan")
                .and_then(|(_, v)| v.as_deref());
            let span = colspan(value);
            if span != 1 {
                self.tree.dom.spans.insert(node, span);
            }
        }
        if let Some(node) = node.filter(|_| ix) {
            let kept: Attributes = atts
                .iter()
                .filter(|(n, _)| IX_ATTRIBUTES.contains(&n.as_slice()))
                .map(|(n, v)| (n.as_slice().into(), v.as_deref().unwrap_or_default().into()))
                .collect();
            if !kept.is_empty() {
                self.tree.dom.attrs.insert(node, kept);
            }
        }
        true
    }

    /// htmlCheckMeta.
    fn check_meta(&mut self, atts: &[(Vec<u8>, Option<Vec<u8>>)]) {
        let mut http = false;
        let mut content = None;
        for (name, value) in atts {
            let Some(value) = value else { continue };
            if name.eq_ignore_ascii_case(b"http-equiv")
                && value.eq_ignore_ascii_case(b"Content-Type")
            {
                http = true;
            } else if name.eq_ignore_ascii_case(b"charset") {
                self.declared_encoding(value);
            } else if name.eq_ignore_ascii_case(b"content") {
                content = Some(value);
            }
        }
        if let (true, Some(content)) = (http, content) {
            // htmlCheckEncoding
            let Some(i) = find_ignore_case(content, b"charset") else {
                return;
            };
            let mut rest = &content[i + 7..];
            if rest.first().is_some_and(|c| is_ws(*c)) {
                match content.iter().position(|&c| c == b'=') {
                    Some(j) => rest = &content[j..],
                    None => return,
                }
            }
            if rest.first() == Some(&b'=') {
                self.declared_encoding(&rest[1..]);
            }
        }
    }

    /// xmlSetDeclaredEncoding.
    fn declared_encoding(&mut self, name: &[u8]) {
        if self.has_encoding {
            return;
        }
        if let Some(enc) = encoding_for(name) {
            self.switch_encoding(enc);
        }
    }

    /// htmlParseElementInternal.
    fn element(&mut self) {
        self.start_tag();
        let Some(name) = self.tree.current() else {
            return;
        };
        if self.peek(0) == b'/' && self.peek(1) == b'>' {
            self.pos += 2;
            self.tree.pop();
            return;
        }
        if self.peek(0) != b'>' {
            return;
        }
        self.pos += 1;
        let info = self.tree.dom.names.info(name);
        let (empty, mode) = (info.empty, info.mode);
        if empty {
            self.tree.pop();
            return;
        }
        self.end_check = mode;
    }

    /// htmlParseEndTag.
    fn end_tag(&mut self) {
        self.end_check = MODE_DATA;
        self.pos += 2;
        if self.at_end() {
            self.tree.start_char_data();
            self.tree.characters(b"</");
            return;
        }
        if self.peek(0) == b'>' {
            self.pos += 1;
            return;
        }
        if !is_letter(self.peek(0)) {
            self.comment(true);
            return;
        }
        self.html_name(false);
        let name = self.tree.dom.names.intern(&self.name);
        self.attributes(false);
        if self.peek(0) == b'>' {
            self.pos += 1;
        } else if self.peek(0) == b'/' && self.peek(1) == b'>' {
            self.pos += 2;
        } else {
            return;
        }
        if self.tree.depth > 0 && (name == HTML || name == BODY || name == HEAD) {
            self.tree.depth -= 1;
            return;
        }
        if !self.tree.open(name) {
            return;
        }
        self.tree.auto_close_on_close(name);
        if self.tree.current() == Some(name) {
            self.tree.pop();
        }
    }

    /// htmlParseCharData (not partial).
    fn char_data(&mut self) {
        let mut mode = self.end_check;
        let mut complete = false;
        let mut eof = false;
        while !self.tree.stopped {
            let chunk = self.pos;
            let mut i = chunk;
            let mut skip = 0;
            let mut repl: &[u8] = b"";
            let mut ncr = 0u32;
            let mut ncr_size = 0;
            let mut cp = 0u32;
            let mut done = false;
            let mut restart = false;
            while !self.tree.stopped {
                if self.end - i <= 64 {
                    if !eof {
                        let avail = self.end - i;
                        self.grow_at(i);
                        eof = self.end - i == avail;
                    }
                    if i >= self.end {
                        done = true;
                        break;
                    }
                }
                if ncr == 0 {
                    while i < self.end && !SPECIAL[self.buf[i] as usize] {
                        i += 1;
                    }
                    if !eof && self.end - i <= 64 || i >= self.end {
                        continue;
                    }
                }
                let c = self.buf[i];
                let mut size = 1;
                if ncr != 0 {
                    let lc = c | 0x20;
                    let digit = if c.is_ascii_digit() {
                        u32::from(c - b'0')
                    } else if ncr == 16 && (b'a'..=b'f').contains(&lc) {
                        u32::from(lc - b'a' + 10)
                    } else {
                        if c == b';' {
                            i += 1;
                            ncr_size += 1;
                        }
                        break;
                    };
                    cp = (cp * ncr + digit).min(0x110000);
                    ncr_size += 1;
                    i += size;
                    continue;
                }
                let at = |k: usize| self.buf[..self.end].get(i + k).copied();
                match c {
                    b'<' => {
                        if mode == MODE_DATA {
                            done = true;
                            complete = true;
                            break;
                        }
                        if mode == MODE_PLAINTEXT {
                        } else if mode == MODE_SCRIPT && at(1) == Some(b'!') {
                            if at(2) == Some(b'-') && at(3) == Some(b'-') {
                                mode = MODE_SCRIPT_ESC1;
                            }
                        } else if at(1).is_some() {
                            let solidus = at(1) == Some(b'/');
                            let mut j = if solidus { 2 } else { 1 };
                            if solidus || mode == MODE_SCRIPT_ESC1 {
                                let current = self
                                    .tree
                                    .current()
                                    .map_or("", |n| self.tree.dom.names.name(n));
                                let name = current.as_bytes();
                                let mut k = 0;
                                while k < name.len() && at(j).is_some_and(|b| name[k] == b | 0x20) {
                                    k += 1;
                                    j += 1;
                                }
                                if k == name.len()
                                    && at(j).is_some_and(|t| t == b'>' || t == b'/' || is_ws(t))
                                {
                                    if mode == MODE_SCRIPT_ESC1 && !solidus {
                                        mode = MODE_SCRIPT_ESC2;
                                    } else if mode == MODE_SCRIPT_ESC2 {
                                        mode = MODE_SCRIPT_ESC1;
                                    } else {
                                        complete = true;
                                        done = true;
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    b'-' => {
                        if (mode == MODE_SCRIPT_ESC1 || mode == MODE_SCRIPT_ESC2)
                            && at(1) == Some(b'-')
                            && at(2) == Some(b'>')
                        {
                            mode = MODE_SCRIPT;
                        }
                    }
                    b'&' if mode == MODE_DATA || mode == MODE_RCDATA => {
                        if at(1) == Some(b'#') {
                            match at(2) {
                                Some(x) if x | 0x20 == b'x' => {
                                    if at(3).is_some_and(|d| d.is_ascii_hexdigit()) {
                                        ncr = 16;
                                        size = 3;
                                        ncr_size = 3;
                                        cp = 0;
                                    }
                                }
                                Some(d) if d.is_ascii_digit() => {
                                    ncr = 10;
                                    size = 2;
                                    ncr_size = 2;
                                    cp = 0;
                                }
                                _ => {}
                            }
                        } else if let Some((n, v)) =
                            entity_prefix(&self.buf[i + 1..self.end], false)
                        {
                            skip = n + 1;
                            repl = v.as_bytes();
                            break;
                        }
                    }
                    0 => {
                        skip = 1;
                        repl = FFFD;
                        break;
                    }
                    b'\r' => {
                        skip = 1;
                        if self.peek_at(i + 1) != b'\n' {
                            repl = b"\n";
                        }
                        break;
                    }
                    c if c >= 0x80 => {
                        if !self.has_encoding {
                            if i == chunk {
                                restart = true;
                            }
                            break;
                        }
                        size = utf8_len(&self.buf[i..self.end]);
                        if size == 0 {
                            skip = 1;
                            repl = FFFD;
                            break;
                        }
                    }
                    _ => {}
                }
                i += size;
            }
            if restart {
                self.guess_encoding();
                eof = false;
                continue;
            }
            let cpbuf: ([u8; 4], usize);
            if ncr_size > 0 {
                skip = ncr_size;
                i -= ncr_size;
                cpbuf = code_point(cp);
                repl = &cpbuf.0[..cpbuf.1];
            }
            if i > chunk {
                self.tree.char_data(&self.buf[chunk..i], mode);
            }
            self.pos = i + skip;
            if !repl.is_empty() {
                self.tree.char_data(repl, mode);
            }
            self.shrink_at(self.pos);
            if done {
                break;
            }
        }
        self.end_check = if complete { MODE_DATA } else { mode };
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(raw: &str) -> String {
        html_to_text(raw.as_bytes()).unwrap()
    }

    #[test]
    fn blocks_and_cells() {
        assert_eq!(
            text("<html><body><p>a</p><p>b<td>c</td><td>d</td></p></body></html>"),
            "a\nb\nc d"
        );
    }

    #[test]
    fn entities() {
        assert_eq!(entity_prefix(b"amp;x", false), Some((4, "&")));
        assert_eq!(entity_prefix(b"notit;", false), Some((3, "\u{ac}")));
        assert_eq!(entity_prefix(b"notin;", false), Some((6, "\u{2209}")));
        assert_eq!(entity_prefix(b"ampx", true), None);
        assert_eq!(entity_prefix(b"ampx", false), Some((3, "&")));
        assert_eq!(
            text("<p>&#x41;&#66&#150;&#0;&nbsp;x</p>"),
            "AB\u{2013}\u{FFFD} x"
        );
    }

    #[test]
    fn hidden_and_dropped() {
        let raw = "<html><head><title>T</title></head><body><div style='display: none'>h</div>\
                   <script>s</script><p>v</p></body></html>";
        assert_eq!(text(raw), "v");
    }

    #[test]
    fn depth_limit_truncates() {
        let deep = |n| {
            format!(
                "<html><body>{}Z{}<p>after</p></body></html>",
                "<span>a".repeat(n),
                "</span>".repeat(n)
            )
        };
        assert!(text(&deep(254)).ends_with("aZafter"));
        assert_eq!(text(&deep(255)), "a".repeat(254));
    }

    #[test]
    fn empty_document() {
        assert_eq!(html_to_text(b"  "), Err(Error::Empty));
    }
}
