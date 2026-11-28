"""Split a filing's primary document into Part/Item sections. Pure, no I/O.

The algorithm is specified precisely so the Rust rewrite (week 4) can match it exactly:

1. html_to_text: remove a leading EDGAR SGML envelope (everything up to and including the
   first <TEXT> tag, when the document starts with <DOCUMENT>); drop <title>, <script>,
   <style>, inline-XBRL <ix:header> and display:none elements;
   a newline after every block element, a space after every table cell; collapse runs of
   spaces/tabs/nbsp/zero-width to one space, trim each line, drop empty lines.
2. Headings are lines starting with "Part <roman>" or "Item <n>[letter]" / "Item <n>.<nn>".
   "Part <roman>" and "Item <n>[letter]" end at a word boundary; "Item <n>.<nn>" only before
   anything but a digit, so a title run on without a space still makes the 8-K item
   ("Item 7.01Regulation FD Disclosure" is 7.01, not 7), and "Item 7.012" is still item 7.
3. Table of contents: tracking the current Part across all headings, if any section key
   occurs twice the body starts at the second occurrence of the first key to repeat (moved
   back to a Part heading at most 50 characters before it). Headings before it are ignored.
   No repeat means no table of contents: the body is the whole document.
4. Body pass, Part reset to none: a Part heading equal to the current Part is a running
   page header and is skipped; so is an Item heading equal to the current section key whose
   same-line title is empty or identical to the current heading's.
   10-Q items seen before any Part heading in the body belong to Part I.
5. For each section key keep the occurrence with the longest span (distance to the next
   kept heading of any kind), which skips line-leading cross-references like "Item 7 of".
6. A section runs from its heading to the next kept section or the next kept Part heading.
7. 10-K MD&A incorporated by reference: when Item 7 is under 2,000 characters and mentions
   "discussion and analysis", MD&A lines are lines of at most 120 characters starting with
   "Management's discussion and analysis", and end lines start with "Management's report on
   internal control" or "Report of independent registered public accounting firm" (case
   insensitive; either apostrophe). Splitting the text at end lines, the stretch with the most
   MD&A lines (at least 2; the earliest on a tie) becomes Item 7: from its first MD&A line to
   the end line that closes the stretch, or the end of the text. Key and title are unchanged.
8. Exhibits (8-K EX-99.x, a 10-K's EX-13, a 6-K's or 40-F's EX-99.x: whatever doc-fetcher
   stores) are parsed on their own: one section per exhibit, key the exhibit type ("EX-99.1"),
   item its number, title the index description or "Exhibit 99.1", text html_to_text of the
   exhibit, offsets into that text.
9. Title headings, for filings that title their items without numbering them ("RISK
   FACTORS"): only when the form starts with "10-K" or "10-Q" and rule 2 finds no Item
   heading. A title heading is a line (the text split at "\n"; it starts at the line's first
   character) of at most 120 characters whose core, folded, is a key of TITLES_10K or
   TITLES_10Q (by form). Core: drop one trailing "." or ":"; then, if what is left ends with
   ")", drop its last "(" through the end when 1-20 characters lie between the two and none
   is ")", plus one space before the "(" if there is one ("... OF OPERATIONS (“MD&A”)").
   Fold: A-Z to a-z, U+0130 and U+0131 to "i", U+017F to "s", U+212A to "k", U+2019 to "'";
   nothing else changes. The title gives the item and its Part (10-Q titles are unique across
   Parts: "Legal Proceedings" is always Part II, item 1).
   Headings: the rule 2 Part headings, except cross-references (the text after the numeral
   and any of " .:-–—" starts with ",", as in "Part I, Item 2"), plus the title headings, in
   text order. Rules 3-6 then apply with these changes for title headings. In rules 3 and 4,
   a title heading whose Part differs from the current Part, when one is set, is skipped: in
   rule 3 it is neither seen nor a repeat, in rule 4 it is not kept (a "Legal Proceedings"
   note inside Part I). Otherwise its key is _key(form, title's Part, item) and its part the
   title's Part, whatever the current Part; its same-line title is empty, so rule 4 skips it
   when its key is the current section's. Its title is the line as written. Rule 7 applies.
   If no title heading is kept in rule 4, the result of rules 2-6 without rule 9 stands
   (no sections, or the error of a text without Part or Item headings).
10. Titles. Only a section's `title` changes here: text, offsets, keys and rules 2-9 (rule 4
   still compares the raw same-line titles) are as above; exhibits (rule 8) keep theirs. A
   section's title starts as its kept heading's: the same-line title, else the next line (rule 2),
   or the line (rule 9); the title's line is the line it was taken from. Then, in order:
   a. Leading characters are dropped while the first is whitespace (str.isspace) or one of
      . , : ; | - * · ) ” ’ ‘ U+005C (backslash) U+2010-U+2015 U+2026 U+200B-U+200F U+FEFF
      ("‑ Exhibits", "… CONTROLS", ", Item", "| Risk Factors", an en space).
   b. Quotes: if it then starts with " or “ and the same quote's closing " or ” occurs later, the
      title is what lies between them, without trailing commas and spaces (“Risk Factors”, and the
      cross-reference ', “Management’s Discussion and Analysis ...,” of our Annual Report'); a
      starting quote with no closing one is dropped.
   c. A title ending in "…" or ".." loses its trailing dots and ellipses, then trailing spaces
      ("CONTROLS AND PROCEDURES….." is a dot leader).
   d. Wrapped titles, forms starting "10-K" or "10-Q": the title's words are rule 9's core of the
      title, folded, split on whitespace. While they are at least one, fewer than, and the start
      of, the words of a title in TITLES_10K (any with this item) or TITLES_10Q (this Part and
      item), the next line after the title's line, if it exists, isn't empty and has at most 120
      characters (as written: rule 1 already trimmed it), is appended with one space between,
      when the result has more words and they are still the start of (or all of) such a title;
      otherwise, or after 3 lines, stop ("MANAGEMENT’S" + "DISCUSSION AND ANALYSIS OF FINANCIAL
      CONDITION AND RESULTS OF OPERATIONS").
   e. The first 200 characters are the title ("" when nothing is left).
11. Tables, beside the text: nothing above changes. On rule 1's tree (after its drops) a table is
   a "table" element, a row a "tr", a cell a "td" or "th" (names read as rule 1 reads them).
   Walking the tree in document order, an element's text, then for each child the child (an
   element: walked the same way) and the child's tail, rebuilds text_content()'s string, the
   content; an element's range in it runs from its text to the end of its last descendant's
   tail (its own tail, rule 1's "\n" or " ", is outside it).
   a. A row belongs to its nearest table ancestor. A cell belongs to its nearest tr ancestor when
      that row's table is also the cell's nearest table ancestor, else to no row. A row is a
      head row when a "thead" element lies between it and its table, or every non-empty cell of
      it is a "th". A cell's span is its colspan attribute's ASCII digits that follow any of
      " \t\n\r\f" at its start, as a number capped to 1-100; 1 without such digits.
   b. Where content lands in the text: each text character comes from one content character,
      a space that replaced a run of rule 1's collapsed characters from the run's first, any
      other character from itself; a "\n" joining two lines comes from none. A cell's
      characters are those that come from its range. Without spaces (U+0020) at either end
      they run from a to b; its text is text[a:b] with every "\n" read as " ", and "" when
      none are left (an empty cell). A row's range runs from the smallest a to the largest b of
      its non-empty cells; a row without one is dropped.
   c. A section's tables: for each table, in document order, its rows whose range lies within
      the section's text (char_start to char_start + len(text)), offsets made relative to that
      text; a table with at least 2 such rows is kept, from its rows' smallest start to their
      largest end. Each keeps all its rows' cells (empty ones and spans too) in order. An
      exhibit's (rule 8) are those of its own text.
12. Form 20-F (forms starting "20-F"). Its items are numbered like a 10-K's (1-19, 4A, 16A-16K,
   unique across Parts), with lettered sub-items ("3.D Risk Factors", "Item 5.A Operating
   Results"):
   a. Sub-item headings are dropped from rule 2's headings before rule 3: they stay in their
      item's text, and a table of contents listing sub-items isn't taken for a repeat. A sub-item
      heading is an Item heading whose item number (the `\\d{1,2}[a-z]?` branch) is followed
      directly by "." and one ASCII letter that is not followed by a word character ("Item 3.D",
      "ITEM 5.A."), or whose item number, upper-cased, ends in something other than a decimal
      digit and isn't 4A or 16A-16K ("Item 5A", "Item 3D").
   b. Item 3.D, Risk Factors: when a section "3" is kept, the first line of it after its first line
      (the text split at "\\n") that is a 3.D heading ends section "3" there and starts section
      "3.D" (key and item "3.D", section 3's part), which runs to section 3's old end; section 3's
      text and 3.D's are their stretches stripped, as in rule 6. A 3.D heading is a line of at most
      120 characters whose rule 9 core, folded, ends with "risk factors" and before it has either
      nothing, or a prefix that loses at least one trailing character of " .:-–—" and, without
      those and without whitespace (str.isspace), is "d", "3d", "3.d", "item3d" or "item3.d"
      ("D. Risk Factors", "3.D RISK FACTORS", "Item 3.D. Risk factors", "Risk Factors"). Its title
      is the line through rule 10 (a-c, e).
   c. A 20-F with no Item heading left after (a) (an annual report laid out by a cross-reference
      index) is one section, as rule 13.
13. Whole documents. A 6-K's or 40-F's primary document (forms starting "6-K" or "40-F": a
   results release or cover page; a 40-F's annual information form, MD&A and statements are
   exhibits, rule 8), and a 20-F's under rule 12c, is one section: key and item the form without
   "/A" ("6-K", "40-F", "20-F"), no part, title "Form 6-K" (the key after "Form "), text the whole
   text, offsets 0 to its length. Rules 2-10 don't apply; rule 11's tables do. A document with no
   text raises, as one without headings does.
14. Printed facts (inline XBRL), beside the text: nothing above changes. A primary document's
   (not an exhibit's) numeric facts where the text prints them. An element's name is rule 11's
   (rule 1's reading, lower case); "*:x" is a name ending in ":x" ("ix:nonfraction").
   a. On rule 1's tree before its drops (lxml.html.fromstring's element and its descendants, in
      document order), a context is a "*:context" element with an "id" attribute, the first of each
      id: its start, end and instant are the text_content(), stripped (str.strip), of its first
      descendant named "*:startdate", "*:enddate" and "*:instant"; it is dimensioned when a
      descendant is named "*:explicitmember" or "*:typedmember". A unit is a "*:unit" element with
      an "id", the first of each id: its measures are the stripped text_content() of its
      descendants named "*:measure", in order, each without what precedes its last ":"
      ("iso4217:USD" is "USD"); the unit is "<first>/<second>" when a descendant is named "*:divide"
      and it has exactly two measures, the measure when it has exactly one, else none.
   b. After the drops, walking the tree as rule 11 does, each "*:nonfraction" element has a range
      in the content and, as a cell's (rule 11b), characters in the text, without spaces at either
      end; its text is them with "\\n" read as " ". It is a printed fact when that text isn't
      empty, its "name" attribute isn't empty, and its "contextref" attribute names a context
      that isn't dimensioned and has a non-empty end (then its period runs from its start, which
      may be absent, to its end) or else a non-empty instant (no start, the instant its end).
   c. Each, in document order: the key of the first section (in the sections' order) whose text
      holds its characters (char_start <= its start and its end <= char_start + len(text)), or
      none; its name (the concept), contextref, unitref and that unit's measure (none when absent
      or unknown); the period's start and end; its scale, sign, decimals and format attributes as
      written (none when absent); its text; and its [start, end) in the document's text.
15. Part I items 1 and 2 of a 10-Q placed by the table of contents (forms starting "10-Q", rules
   2-6's headings, not rule 9's): when rule 3 found a table of contents (the body starts at heading
   index s > 0) and rule 4's body has an Item heading. The contents are rule 2's headings before s,
   each Item heading with the Part tracked as in rule 3 (a Part heading sets it; none before the
   first). The gap runs from the end of the contents' last heading's title line (an Item heading's
   title_end, rule 10; a Part heading's own line) to the body's first heading. Lines are the text
   split at "\\n"; a line "after p" starts after offset p. For item "1", then "2", when no body
   Item heading has key "I-<n>": its contents heading is the first contents Item heading of that
   item whose Part is "I" or none. Its entries are the lines after its title_end that start before
   the next contents heading (or the gap's start, when it is the last), except a line of only
   ASCII digits and spaces, and a line whose rule 9 core, folded, is "page". A gap line (one that
   starts after the gap's start and before its end) of at most 120 characters matches when its
   core, folded (rule 9), is not empty and equals an entry's, or has at least 2 words (split on
   whitespace, str.split) and, followed by one space, starts an entry's. The first matching gap
   line, unless the other item took it, starts a body heading for the item: key "I-<n>", Part "I",
   its title (and the line its title ends) the contents heading's. Rules 5-7 then apply to the
   body with these headings in text order (JPMorgan's 10-Q lists Item 1's statements and Item 2's
   "Consolidated Financial Highlights" in its contents and heads neither with an Item line).
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace

from lxml import html

# 9: printed facts beside the text (rule 14; the text is 8's), a 10-Q's Part I items 1 and 2 placed
# by its contents (rule 15); 8: 20-F items with 3.D and sub-item headings dropped (rule 12), 6-K /
# 40-F whole documents (rule 13); 7: tables beside the text (rule 11; the text is 6's); 6: "Item
# 7.01Regulation" is 7.01 (rule 2); 5: clean titles (rule 10); 4: title headings
# (rule 9); 3: MD&A by reference, exhibits, SGML
PARSER_VERSION = "python-9"

_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
_CELL = {"td", "th"}
_ENVELOPE = re.compile(rb"^\s*<DOCUMENT>.*?<TEXT>", re.I | re.S)
_DROP = (
    "//title | //script | //style | //*[local-name()='header']"
    " | //*[contains(translate(@style, ' ', ''), 'display:none')]"
)
_MDNA = re.compile(r"^management[’']s discussion and analysis\b.*$", re.I | re.M)
_MDNA_END = re.compile(
    r"^(?:management[’']s report on internal control"
    r"|report of independent registered public accounting firm)\b",
    re.I | re.M,
)
_SPACES = re.compile(r"[ \t\xa0​  ]+")
_HEADING = re.compile(
    r"^(?:part\s+(?P<part>iv|i{1,3})\b|item\s+(?P<item>\d{1,2}\.\d{2}(?!\d)|\d{1,2}[a-z]?\b))"
    r"[ .:\-–—]*(?P<rest>.*)$",
    re.IGNORECASE | re.MULTILINE,
)


_MDNA_TITLES = (
    "management's discussion and analysis of financial condition and results of operations",
    "management's discussion and analysis of results of operations and financial condition",
)
_MARKET_RISK = (
    "quantitative and qualitative disclosures about market risk",
    "quantitative and qualitative disclosure about market risk",
)
# rule 9: folded title -> (Part, item). Official Form 10-K / 10-Q item titles plus a few
# common spellings.
TITLES_10K = {
    "business": ("I", "1"),
    "risk factors": ("I", "1A"),
    "unresolved staff comments": ("I", "1B"),
    "cybersecurity": ("I", "1C"),
    "properties": ("I", "2"),
    "legal proceedings": ("I", "3"),
    "mine safety disclosures": ("I", "4"),
    "market for registrant's common equity, related stockholder matters and issuer purchases"
    " of equity securities": ("II", "5"),
    "selected financial data": ("II", "6"),
    **dict.fromkeys(_MDNA_TITLES, ("II", "7")),
    **dict.fromkeys(_MARKET_RISK, ("II", "7A")),
    "financial statements and supplementary data": ("II", "8"),
    "changes in and disagreements with accountants on accounting and financial disclosure":
        ("II", "9"),
    "controls and procedures": ("II", "9A"),
    "other information": ("II", "9B"),
    "disclosure regarding foreign jurisdictions that prevent inspections": ("II", "9C"),
    "directors, executive officers and corporate governance": ("III", "10"),
    "executive compensation": ("III", "11"),
    "security ownership of certain beneficial owners and management and related stockholder"
    " matters": ("III", "12"),
    "certain relationships and related transactions, and director independence": ("III", "13"),
    "principal accountant fees and services": ("III", "14"),
    "exhibits and financial statement schedules": ("IV", "15"),
    "exhibit and financial statement schedules": ("IV", "15"),
    "exhibits, financial statement schedules": ("IV", "15"),
    "form 10-k summary": ("IV", "16"),
}
TITLES_10Q = {
    "financial statements": ("I", "1"),
    "consolidated financial statements": ("I", "1"),
    "condensed consolidated financial statements": ("I", "1"),
    "unaudited condensed consolidated financial statements": ("I", "1"),
    "unaudited consolidated financial statements": ("I", "1"),
    **dict.fromkeys(_MDNA_TITLES, ("I", "2")),
    **dict.fromkeys(_MARKET_RISK, ("I", "3")),
    "controls and procedures": ("I", "4"),
    "legal proceedings": ("II", "1"),
    "risk factors": ("II", "1A"),
    "unregistered sales of equity securities and use of proceeds": ("II", "2"),
    "defaults upon senior securities": ("II", "3"),
    "mine safety disclosures": ("II", "4"),
    "other information": ("II", "5"),
    "exhibits": ("II", "6"),
}
# rule 12a: a sub-item letter right after an item number ("Item 3.D", "ITEM 5.A.")
_SUB_ITEM = re.compile(r"\.[A-Za-z](?!\w)")
# rule 12a: the lettered items of a 20-F (any other letter makes a sub-item: "Item 5A")
_LETTERED_20F = frozenset({"4A"} | {f"16{c}" for c in "ABCDEFGHIJK"})
# rule 12b: what may come before "risk factors" on a 3.D heading line, its separators and
# whitespace taken out
_RISK_PREFIXES = frozenset({"d", "3d", "3.d", "item3d", "item3.d"})
_SEPARATORS = " .:-\u2013\u2014"
_FOLD = str.maketrans({**{chr(c): chr(c + 32) for c in range(ord("A"), ord("Z") + 1)},
                       "\u0130": "i", "\u0131": "i", "\u017f": "s", "\u212a": "k",
                       "\u2019": "'"})


@dataclass(frozen=True)
class Cell:
    """Rule 11: one <td> or <th>."""
    text: str      # its characters in the text, "\n" read as " "; "" when it has none
    span: int = 1  # its colspan, 1-100


@dataclass(frozen=True)
class Row:
    """Rule 11: one <tr> with at least one non-empty cell."""
    start: int     # [start, end) in the section's text: its first to last non-empty cell
    end: int
    head: bool     # inside a <thead> of its table, or every non-empty cell a <th>
    cells: list[Cell]


@dataclass(frozen=True)
class Table:
    """Rule 11: one <table>'s rows that lie in a section."""
    start: int     # [start, end) in the section's text: its first row's start to last row's end
    end: int
    rows: list[Row]


@dataclass(frozen=True)
class Section:
    key: str          # 10-K: "1A"; 10-Q: "II-1A"; 8-K: "2.02"
    part: str | None  # "I", "II", ... when known
    item: str
    title: str
    text: str
    char_start: int   # offsets into html_to_text(document)
    char_end: int
    tables: list[Table] = field(default_factory=list)  # rule 11


@dataclass(frozen=True)
class PrintedFact:
    """Rule 14: an inline XBRL numeric fact (ix:nonFraction) where the text prints it."""
    section: str | None  # the key of the section whose text holds it
    concept: str         # its name attribute: "us-gaap:Revenues"
    context: str         # contextref
    unit_ref: str | None  # unitref
    unit: str | None     # the unit's measure: "USD", "USD/shares", "shares"
    start: str | None    # the period's start (a duration), none for an instant
    end: str             # the period's end, or the instant
    scale: str | None    # attributes as written
    sign: str | None
    decimals: str | None
    format: str | None
    text: str            # its characters in the text, "\n" read as " "
    char_start: int      # [char_start, char_end) in html_to_text(document)
    char_end: int


def _name(el) -> str:
    return el.tag.rsplit("}", 1)[-1].lower()


def _tree(raw: bytes, header: list | None = None):
    """Rule 1's tree: the document without its envelope and dropped elements, a "\\n" tail on
    every block and a " " tail on every cell. With `header`, rule 14a's contexts and units read
    before the drops are appended to it."""
    doc = html.fromstring(_ENVELOPE.sub(b"", raw, count=1))
    if header is not None:
        header.extend(_header(doc))
    for el in doc.xpath(_DROP):
        el.drop_tree()
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in _BLOCK:
            el.tail = "\n" + (el.tail or "")
        elif tag in _CELL:
            el.tail = " " + (el.tail or "")
    return doc


def _lines(content: str) -> str:
    lines = (_SPACES.sub(" ", line).strip() for line in content.split("\n"))
    return "\n".join(line for line in lines if line)


def html_to_text(raw: bytes) -> str:
    return _lines(_tree(raw).text_content())


def extract(raw: bytes) -> tuple[str, list[Table]]:
    """Rule 1's text and rule 11's tables, row and table offsets into that text."""
    text, tables, _ = _extract(raw)
    return text, tables


def _extract(raw: bytes) -> tuple[str, list[Table], list[PrintedFact]]:
    """Rule 1's text, rule 11's tables and rule 14's printed facts (sections not yet assigned)."""
    header: list = []
    doc = _tree(raw, header)
    contexts, units = header
    content = doc.text_content()
    text = _lines(content)
    walked, tables, marks = _walk(doc)
    if walked != content:  # never seen; the text is text_content()'s whatever happens
        return text, [], []
    where = _Where(content, text)

    def land(cs: int, ce: int) -> tuple[int, int]:
        a, b = where.first(cs), where.end(ce)
        while a < b and text[a] == " ":
            a += 1
        while b > a and text[b - 1] == " ":
            b -= 1
        return a, b

    out = []
    for rows in tables:
        kept = []
        for head, cells in rows:
            spans, row_cells = [], []
            for cs, ce, span, th in cells:
                a, b = land(cs, ce)
                row_cells.append(Cell(text[a:b].replace("\n", " ") if a < b else "", span))
                if a < b:
                    spans.append((a, b, th))
            if spans:
                kept.append(Row(min(a for a, _, _ in spans), max(b for _, b, _ in spans),
                                head or all(th for _, _, th in spans), row_cells))
        if kept:
            out.append(Table(min(r.start for r in kept), max(r.end for r in kept), kept))
    facts = []
    for cs, ce, el in marks:  # rule 14b
        name, ref = el.get("name"), el.get("contextref")
        context = contexts.get(ref) if ref is not None else None
        if not name or context is None or context[3]:
            continue
        start, end, instant, _ = context
        if not end:
            if not instant:
                continue
            start, end = None, instant
        a, b = land(cs, ce)
        if a >= b:
            continue
        unit_ref = el.get("unitref")
        facts.append(PrintedFact(
            None, name, ref, unit_ref, units.get(unit_ref) if unit_ref is not None else None,
            start, end, el.get("scale"), el.get("sign"), el.get("decimals"), el.get("format"),
            text[a:b].replace("\n", " "), a, b))
    return text, out, facts


def _header(doc) -> tuple[dict, dict]:
    """Rule 14a: contexts (id -> (start, end, instant, dimensioned)) and units (id -> measure)."""
    contexts: dict[str, tuple] = {}
    units: dict[str, str | None] = {}
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        name = _name(el)
        if name.endswith(":context"):
            ref = el.get("id")
            if ref is not None and ref not in contexts:
                contexts[ref] = _context(el)
        elif name.endswith(":unit"):
            ref = el.get("id")
            if ref is not None and ref not in units:
                units[ref] = _unit(el)
    return contexts, units


def _context(el) -> tuple:
    found: dict[str, str] = {}
    dimensioned = False
    for d in el.iterdescendants():
        if not isinstance(d.tag, str):
            continue
        name = _name(d)
        for part in ("startdate", "enddate", "instant"):
            if name.endswith(":" + part) and part not in found:
                found[part] = d.text_content().strip()
        if name.endswith((":explicitmember", ":typedmember")):
            dimensioned = True
    return found.get("startdate"), found.get("enddate"), found.get("instant"), dimensioned


def _unit(el) -> str | None:
    measures, divide = [], False
    for d in el.iterdescendants():
        if not isinstance(d.tag, str):
            continue
        name = _name(d)
        if name.endswith(":measure"):
            measures.append(d.text_content().strip().rsplit(":", 1)[-1])
        elif name.endswith(":divide"):
            divide = True
    if divide and len(measures) == 2:
        return f"{measures[0]}/{measures[1]}"
    return measures[0] if len(measures) == 1 else None


def _colspan(el) -> int:
    m = _COLSPAN.match(el.get("colspan") or "")
    return min(max(int(m.group(1)), 1), 100) if m else 1


_COLSPAN = re.compile(r"[ \t\n\r\f]*([0-9]+)")


def _walk(root) -> tuple[str, list, list]:
    """text_content() rebuilt by walking the tree, rule 11's tables in document order: each
    its rows, (thead, [(start, end, colspan, is_th)]) with [start, end) ranges in that text, and
    rule 14b's "*:nonfraction" elements, [start, end, element], in document order."""
    parts: list[str] = []
    n = 0
    tables: list = []
    facts: list[list] = []
    open_tables: list[list] = []  # per open <table>: [its rows, the open row, open <thead>s]

    def visit(el) -> None:
        nonlocal n
        tag = el.tag.rsplit("}", 1)[-1].lower()
        mark = top = saved = None
        fact = [n, n, el] if tag.endswith(":nonfraction") else None
        if fact is not None:
            facts.append(fact)
        if tag == "table":
            top = [[], None, 0]
            tables.append(top[0])
            open_tables.append(top)
        elif open_tables:
            top = open_tables[-1]
            if tag == "thead":
                top[2] += 1
            elif tag == "tr":
                saved = top[1]
                top[1] = (top[2] > 0, [])
                top[0].append(top[1])
            elif tag in _CELL and top[1] is not None:
                mark = [n, n, _colspan(el), tag == "th"]
                top[1][1].append(mark)
        if el.text:
            parts.append(el.text)
            n += len(el.text)
        for child in el:
            if isinstance(child.tag, str):
                visit(child)
            if child.tail:
                parts.append(child.tail)
                n += len(child.tail)
        if mark is not None:
            mark[1] = n
        if fact is not None:
            fact[1] = n
        if tag == "table":
            open_tables.pop()
        elif top is not None and tag == "thead":
            top[2] -= 1
        elif top is not None and tag == "tr":
            top[1] = saved

    visit(root)
    return "".join(parts), [[(head, [tuple(c) for c in cells]) for head, cells in rows]
                            for rows in tables], facts


class _Where:
    """Rule 11's offsets: where characters of the content (text_content()) land in the text.
    A text character comes from one content character: a collapsed space from the first of its
    run, any other character from itself; the "\\n" joining two lines from none."""

    def __init__(self, content: str, text: str):
        self.content, self.text = content, text
        self.raw: list[int] = []      # each content line's start
        self.at: list[int] = []       # its text line's start in the text, -1 when dropped
        self.lead: list[int] = []     # characters strip() took from the collapsed line's start
        self.size: list[int] = []     # its text line's length
        self.runs: dict[int, tuple[list[int], list[int], list[int]]] = {}
        raw = at = 0
        first = True
        for line in content.split("\n"):
            collapsed = _SPACES.sub(" ", line)
            kept = collapsed.strip()
            self.raw.append(raw)
            if kept:
                if not first:
                    at += 1  # the "\n" joining it to the line before
                first = False
                self.at.append(at)
                self.lead.append(len(collapsed) - len(collapsed.lstrip()))
                self.size.append(len(kept))
                at += len(kept)
            else:
                self.at.append(-1)
                self.lead.append(0)
                self.size.append(0)
            raw += len(line) + 1
        # the next kept line's start from each line on, the previous kept line's end before it
        self.next = [len(text)] * (len(self.raw) + 1)
        for i in range(len(self.raw) - 1, -1, -1):
            self.next[i] = self.at[i] if self.at[i] >= 0 else self.next[i + 1]
        self.prev = [0] * len(self.raw)
        end = 0
        for i in range(len(self.raw)):
            self.prev[i] = end
            if self.at[i] >= 0:
                end = self.at[i] + self.size[i]

    def _collapsed(self, i: int, r: int) -> int:
        """Characters of line i's collapsed form that come from its first r characters."""
        if i not in self.runs:
            line = self.content[self.raw[i]:self.raw[i + 1] - 1 if i + 1 < len(self.raw)
                                else len(self.content)]
            starts, ends, before = [], [], [0]
            for m in _SPACES.finditer(line):
                starts.append(m.start())
                ends.append(m.end())
                before.append(before[-1] + m.end() - m.start())
            self.runs[i] = (starts, ends, before)
        starts, ends, before = self.runs[i]
        k = bisect_left(starts, r)  # runs starting before r
        in_runs = before[k] - (max(0, ends[k - 1] - r) if k else 0)
        return r - in_runs + k

    def first(self, p: int) -> int:
        """The text index of the first character that comes from content position p or later
        (len(text) when none)."""
        i = bisect_right(self.raw, p) - 1
        if self.at[i] >= 0:
            c = self._collapsed(i, p - self.raw[i]) - self.lead[i]
            if c < self.size[i]:
                return self.at[i] + max(c, 0)
        return self.next[i + 1]

    def end(self, q: int) -> int:
        """One past the text index of the last character that comes from before content
        position q (0 when none)."""
        i = bisect_right(self.raw, q) - 1
        if self.at[i] >= 0:
            c = min(self._collapsed(i, q - self.raw[i]) - self.lead[i], self.size[i])
            if c > 0:
                return self.at[i] + c
        return self.prev[i]


@dataclass
class _Heading:
    start: int
    part: str | None   # Part heading: its numeral; Item heading: unused until the body pass
    item: str | None   # None for a Part heading
    title: str
    inline_title: str = ""  # text after the item number on the heading's own line
    key: str = ""
    span: int = 0
    title_part: str | None = None  # rule 9 title heading: the Part its title belongs to
    xref: bool = False  # Part heading followed by ",": "Part I, Item 2" (rule 9 ignores it)
    title_end: int = 0  # rule 10: where the title's line ends (its "\n", or the text's end)
    sub: bool = False  # rule 12a: an item number followed by ".<letter>" ("Item 3.D")


def find_sections(text: str, form: str) -> list[Section]:
    if form.startswith(("6-K", "40-F")):
        return [_whole(text, form)]  # rule 13
    headings = list(_headings(text))
    if form.startswith("20-F"):
        headings = [h for h in headings if not _sub_item_20f(h)]  # rule 12a
        if all(h.item is None for h in headings):
            return [_whole(text, form)]  # rule 12c
    if form.startswith(("10-K", "10-Q")) and all(h.item is None for h in headings):
        titles = list(_title_headings(text, form))  # rule 9
        if titles:
            merged = sorted([h for h in headings if not h.xref] + titles, key=lambda h: h.start)
            body = _body(merged, form)
            if any(h.item is not None for h in body):
                return _sections(text, body, form)
    start = _contents_end(headings, form)
    body = _body(headings, form, start)
    if form.startswith("10-Q"):
        body = _contents_items(text, headings, start, body)  # rule 15
    return _sections(text, body, form)


def _contents_items(text: str, headings: list[_Heading], start: int,
                    body: list[_Heading]) -> list[_Heading]:
    """Rule 15: Part I items 1 and 2 the body has no heading for, placed by their entries in the
    table of contents."""
    if start == 0 or not any(h.item is not None for h in body):
        return body
    keys = {h.key for h in body if h.item is not None}
    missing = [n for n in ("1", "2") if f"I-{n}" not in keys]
    if not missing:
        return body
    contents = headings[:start]
    parts, part = [], None  # each contents heading's Part, as rule 3 tracks it
    for h in contents:
        if h.item is None:
            part = h.part
        parts.append(part)
    last = contents[-1]
    gap = last.title_end if last.item is not None else _line_end(text, last.start)
    if gap >= body[0].start:
        return body
    added: list[_Heading] = []
    for n in missing:
        i = next((i for i, h in enumerate(contents)
                  if h.item == n and parts[i] in (None, "I")), None)
        if i is None:
            continue
        toc = contents[i]
        entries = _entries(text, toc.title_end, contents[i + 1].start if i + 1 < start else gap)
        at = _entry_line(text, gap, body[0].start, entries, {h.start for h in added})
        if at is not None:
            added.append(replace(toc, start=at, part="I", key=f"I-{n}", span=0))
    return sorted(body + added, key=lambda h: h.start) if added else body


def _line_end(text: str, p: int) -> int:
    nl = text.find("\n", p)
    return len(text) if nl == -1 else nl


def _lines_after(text: str, p: int, stop: int):
    """Rule 15: (start, line) for each line that starts after p and before stop."""
    q = p + 1
    while q < stop:
        end = _line_end(text, q)
        yield q, text[q:end]
        q = end + 1


def _entries(text: str, title_end: int, stop: int) -> list[str]:
    """Rule 15: a contents heading's entries, as rule 9 cores, folded."""
    out = []
    for _, line in _lines_after(text, title_end, stop):
        core = _title_core(line).translate(_FOLD)
        if all(c in "0123456789 " for c in line) or core == "page":
            continue
        out.append(core)
    return out


def _entry_line(text: str, gap: int, stop: int, entries: list[str],
                taken: set[int]) -> int | None:
    """Rule 15: where the first gap line that matches an entry starts."""
    if not entries:
        return None
    exact = set(entries)
    for at, line in _lines_after(text, gap, stop):
        if len(line) > 120 or at in taken:
            continue
        core = _title_core(line).translate(_FOLD)
        if core and (core in exact or len(core.split()) >= 2
                     and any(e.startswith(core + " ") for e in entries)):
            return at
    return None


def _sections(text: str, body: list[_Heading], form: str) -> list[Section]:
    """Rules 5-7 on the body's headings."""
    for h, nxt in zip(body, body[1:] + [None], strict=True):
        h.span = (nxt.start if nxt else len(text)) - h.start

    best: dict[str, _Heading] = {}
    for h in body:
        if h.item is not None and (h.key not in best or h.span > best[h.key].span):
            best[h.key] = h

    kept = sorted(best.values(), key=lambda h: h.start)
    part_starts = [h.start for h in body if h.item is None]
    sections = []
    for i, h in enumerate(kept):
        end = kept[i + 1].start if i + 1 < len(kept) else len(text)
        end = min([end] + [p for p in part_starts if h.start < p < end])
        sections.append(Section(h.key, h.part, h.item, _title(h, text, form),
                                text[h.start:end].strip(), h.start, end))
    if form.startswith("10-K"):
        sections = [_mdna_by_reference(text, s) if s.key == "7" else s for s in sections]
    if form.startswith("20-F"):
        sections = _risk_factors_3d(text, sections, form)
    return sections


def _sub_item_20f(h: _Heading) -> bool:
    """Rule 12a."""
    if h.item is None:
        return False
    return h.sub or not (h.item[-1].isdecimal() or h.item in _LETTERED_20F)


def _whole(text: str, form: str) -> Section:
    """Rule 13."""
    if not text:
        raise ValueError("document has no text")
    key = form.split("/", 1)[0]
    return Section(key, None, key, f"Form {key}", text, 0, len(text))


def _risk_factors_3d(text: str, sections: list[Section], form: str) -> list[Section]:
    """Rule 12b."""
    at = next((i for i, s in enumerate(sections) if s.key == "3"), None)
    if at is None:
        return sections
    item3 = sections[at]
    first = text.find("\n", item3.char_start)
    p = first + 1 if first != -1 else item3.char_end
    while p < item3.char_end:
        nl = text.find("\n", p)
        line = text[p:nl if nl != -1 else len(text)]
        if _is_3d_heading(line):
            h = _Heading(p, item3.part, "3.D", line, title_end=p + len(line))
            risk = Section("3.D", item3.part, "3.D", _title(h, text, form),
                           text[p:item3.char_end].strip(), p, item3.char_end)
            item3 = replace(item3, text=text[item3.char_start:p].strip(), char_end=p)
            return sections[:at] + [item3, risk] + sections[at + 1:]
        p += len(line) + 1
    return sections


def _is_3d_heading(line: str) -> bool:
    """Rule 12b: "D. Risk Factors", "3.D RISK FACTORS", "Item 3.D. Risk factors", "Risk Factors"."""
    if len(line) > 120:
        return False
    core = _title_core(line).translate(_FOLD)
    if not core.endswith("risk factors"):
        return False
    head = core[:-len("risk factors")]
    if not head:
        return True
    bare = head.rstrip(_SEPARATORS)
    return len(bare) < len(head) and "".join(bare.split()) in _RISK_PREFIXES


def _mdna_by_reference(text: str, item7: Section) -> Section:
    """Rule 7."""
    if len(item7.text) >= 2000 or "discussion and analysis" not in item7.text.lower():
        return item7
    ends = [m.start() for m in _MDNA_END.finditer(text)]
    stretches: dict[int, list[int]] = {}  # closing end line (or len(text)) -> MD&A lines
    for m in _MDNA.finditer(text):
        if len(m.group(0)) > 120:
            continue
        close = next((e for e in ends if e > m.start()), len(text))
        stretches.setdefault(close, []).append(m.start())
    if not stretches:
        return item7
    close, lines = max(stretches.items(), key=lambda kv: (len(kv[1]), -kv[0]))
    if len(lines) < 2:
        return item7
    start = lines[0]
    return Section(item7.key, item7.part, item7.item, item7.title, text[start:close].strip(),
                   start, close)


def exhibit_section(exhibit_type: str, description: str | None, raw: bytes) -> Section:
    """Rule 8 (and rule 11's tables)."""
    text, tables = extract(raw)
    item = exhibit_type.removeprefix("EX-")
    section = Section(exhibit_type, None, item, description or f"Exhibit {item}", text, 0,
                      len(text))
    return _with_tables(section, tables)


def _headings(text: str):
    for m in _HEADING.finditer(text):
        if m.group("part"):
            yield _Heading(m.start(), m.group("part").upper(), None, "",
                           xref=m.group("rest").startswith(","))
            continue
        inline = title = m.group("rest").strip()
        title_end = m.end()
        sub = "." not in m.group("item") and _SUB_ITEM.match(text, m.end("item")) is not None
        if not title:  # title on the next line, as in many tables of contents
            nxt = text.find("\n", m.end() + 1)
            title_end = nxt if nxt != -1 else len(text)
            title = text[m.end() + 1 : title_end].strip()
        yield _Heading(m.start(), None, m.group("item").upper(), title[:200], inline,
                       title_end=title_end, sub=sub)


def _title_headings(text: str, form: str):
    """Rule 9: lines that are a standard item title."""
    titles = TITLES_10Q if form.startswith("10-Q") else TITLES_10K
    pos = 0
    for line in text.split("\n"):
        if len(line) <= 120 and (hit := titles.get(_title_core(line).translate(_FOLD))):
            yield _Heading(pos, None, hit[1], line, title_part=hit[0], title_end=pos + len(line))
        pos += len(line) + 1


# rule 10a: what a title may not start with (besides whitespace)
_TITLE_LEAD = frozenset(".,:;|-*·)\\”’‘…​‌‍‎‏﻿"
                        + "".join(map(chr, range(0x2010, 0x2016))))
_CLOSE_QUOTE = {'"': '"', "“": "”"}


def _title(h: _Heading, text: str, form: str) -> str:
    """Rule 10."""
    t, i = h.title, 0
    while i < len(t) and (t[i] in _TITLE_LEAD or t[i].isspace()):
        i += 1
    t = t[i:]
    if t[:1] in _CLOSE_QUOTE:
        close = t.find(_CLOSE_QUOTE[t[0]], 1)
        t = t[1:close].rstrip(", ") if close != -1 else t[1:]
    if t.endswith(("…", "..")):
        t = t.rstrip(".…").rstrip(" ")
    if form.startswith(("10-K", "10-Q")) and h.item is not None:
        q = form.startswith("10-Q")
        wanted = [title.split() for title, (part, item) in (TITLES_10Q if q else TITLES_10K).items()
                  if item == h.item and (not q or part == h.part)]
        words, end = _title_words(t), h.title_end
        for _ in range(3):
            if not words or end >= len(text) or not any(
                    len(words) < len(w) and w[:len(words)] == words for w in wanted):
                break
            nxt = text.find("\n", end + 1)
            nxt = len(text) if nxt == -1 else nxt
            line = text[end + 1:nxt]
            more = _title_words(t + " " + line)
            if (not line or len(line) > 120 or len(more) <= len(words)
                    or not any(w[:len(more)] == more for w in wanted)):
                break
            t, words, end = t + " " + line, more, nxt
    return t[:200]


def _title_words(title: str) -> list[str]:
    return _title_core(title).translate(_FOLD).split()


def _title_core(line: str) -> str:
    if line.endswith((".", ":")):
        line = line[:-1]
    if line.endswith(")"):
        i = line.rfind("(")
        if i != -1 and 1 <= len(line) - i - 2 <= 20 and ")" not in line[i + 1:-1]:
            line = line[:i].removesuffix(" ")
    return line


def _contents_end(headings: list[_Heading], form: str) -> int:
    """Rule 3: the index of the heading the body starts at (0: no table of contents)."""
    seen, part, start = set(), None, 0
    for i, h in enumerate(headings):
        if h.item is None:
            part = h.part
            continue
        if h.title_part and part and part != h.title_part:
            continue
        key = _key(form, h.title_part or part, h.item)
        if key in seen:
            start = i
            if i > 0 and headings[i - 1].item is None and h.start - headings[i - 1].start <= 50:
                start = i - 1
            break
        seen.add(key)
    return start


def _body(headings: list[_Heading], form: str, start: int | None = None) -> list[_Heading]:
    """Headings after the table of contents (rule 3), with page headers dropped and keys
    assigned (rule 4)."""
    if start is None:
        start = _contents_end(headings, form)
    body: list[_Heading] = []
    part, current = None, None
    for h in headings[start:]:
        if h.item is None:
            if h.part == part:
                continue
            part = h.part
            body.append(h)
            continue
        if h.title_part:
            if part and part != h.title_part:
                continue
            h.part = h.title_part
        else:
            h.part = part or ("I" if form.startswith("10-Q") else None)
        h.key = _key(form, h.part, h.item)
        if current and h.key == current.key and h.inline_title in ("", current.inline_title):
            continue
        current = h
        body.append(h)
    return body


def parse(raw: bytes, form: str) -> list[Section]:
    return parse_document(raw, form)[0]


def parse_document(raw: bytes, form: str) -> tuple[list[Section], list[PrintedFact]]:
    """A primary document's sections (rules 1-13, 15, with rule 11's tables) and printed facts
    (rule 14)."""
    text, tables, facts = _extract(raw)
    sections = [_with_tables(s, tables) for s in find_sections(text, form)]
    return sections, [replace(f, section=_section_of(sections, f)) for f in facts]


def _section_of(sections: list[Section], fact: PrintedFact) -> str | None:
    """Rule 14c: the first section whose text holds the fact's characters."""
    return next((s.key for s in sections if s.char_start <= fact.char_start
                 and fact.char_end <= s.char_start + len(s.text)), None)


def _with_tables(section: Section, tables: list[Table]) -> Section:
    """Rule 11: the rows of each table that lie within the section's text, offsets into it."""
    lo, hi = section.char_start, section.char_start + len(section.text)
    out = []
    for t in tables:
        rows = [replace(r, start=r.start - lo, end=r.end - lo) for r in t.rows
                if r.start >= lo and r.end <= hi]
        if len(rows) >= 2:
            out.append(Table(min(r.start for r in rows),
                             max(r.end for r in rows), rows))
    return replace(section, tables=out)


def _key(form: str, part: str | None, item: str) -> str:
    # 10-Q item numbers restart in each Part; 10-K and 8-K item numbers are unique
    if form.startswith("10-Q"):
        return f"{part or '?'}-{item}"
    return item
