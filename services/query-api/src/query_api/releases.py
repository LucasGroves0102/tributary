"""What an earnings release says about its outlook and management's adjustments, found in its
text by fixed rules (no model): quoted passages with their offsets, never a figure read out of
them. Pure; the text is the parser's (lines joined by "\\n", one line per block element, table
cells separated by spaces: filing_parser.sections.html_to_text).

Rules (constants below):

1. Lines. The text split at "\\n"; a line's offsets are into the text. Blank lines and page
   numbers (a line of 1-3 digits) are skipped everywhere.
2. A heading line: 3-160 characters with no "$" or "%", not starting with a bullet ("•◦▪●*-–")
   or "(", not ending in ".", ",", ";" or ":", at most 20 words, letters at least half of its
   non-space characters. A value line: at most 3 letters and a digit (a table cell on its own
   line, "49% - 51%"). Prose: 120 characters or more, ending in ".", ")", "”" or '"'. A
   dateline: a line whose first 120 characters hold a date ("Oct. 28, 2026", "November 3, 2026")
   followed by a dash ("–", "—", "―", "--", "-"), or a wire service's name ("Business Wire",
   "PRNewswire", "GlobeNewswire", "ACCESSWIRE"): where a release's text begins.
3. Headline: the first of the first 6 lines that is 20-300 characters and doesn't start with
   "Exhibit 99", "EX-99", "For immediate release", "Press release", "News release", "Media
   release", "Contact" (case-insensitive) or a label ("Investors: Jim ...": up to 25 letters and
   spaces, then ": "). None otherwise.
4. Adjustments, tables first. A reconciliation heading is a heading line (rule 2, up to 200
   characters) that contains "reconcil" and doesn't start with "Adjustments to reconcile" (a cash
   flow statement's line). Its block runs from the heading through the following lines and ends
   before the first of: another reconciliation heading; a heading line followed by prose (the
   next line) that doesn't start with a footnote mark (rule 5's); a heading line that doesn't
   contain "reconcil", "non-gaap", "adjusted" or "gaap" and isn't a column header itself,
   followed by a column header (a line containing "months ended", "weeks ended", "quarter
   ended", "quarters ended" or "year ended", or of years and dates only: every word a year, a
   month or a day, at least one a year or a month): another table's title; MAX_BLOCK_LINES lines
   or MAX_BLOCK_CHARS characters (then `truncated`). Heading lines at the end of a block that
   aren't followed by a value line (a company name, the next table's title) are dropped.
5. Adjustment notes. Before the financial statements (the first line starting with "Condensed
   consolidated", "Consolidated statements", "Consolidated balance", "Unaudited condensed",
   "Statements of operations" or "Forward-looking statements", case-insensitive; else the whole
   text), a line of 80 characters or more that starts with a footnote mark ("(1)", "(12)",
   "(A)", "*", "**"), lies outside every block of rules 4 and 6 (a note in an outlook block is
   quoted there) and holds a dollar amount ("$" then a digit). At most MAX_NOTES, in text order.
6. Outlook. An outlook heading is a heading line (rule 2, up to 100 characters; when it starts a
   block, one ending in ":" too) that starts with a capital or a digit, has no ". " inside, holds
   "outlook" or "guidance" as a word and none of "reconcil", "non-gaap", "forward-looking", "safe
   harbor", "cautionary", and lies outside every rule 4 block. Its block takes the lines after it
   until one of these ends it: another outlook heading (not ending in ":"; it starts a block of
   its own); a dateline; a section heading (a heading line starting with "Highlights",
   "Conference call", "Webcast", "About", "Forward-looking", "Non-GAAP", "Investor", "Contacts",
   "Dividend", "Capital allocation", "Share repurchase", "Balance sheet", "Liquidity", "Cash
   flow", "Segment", "Results", "Summary", "CFO commentary", "First/Second/Third/Fourth quarter"
   unless an outlook follows, ...: _SECTION); a heading line followed by prose that doesn't start
   with a footnote mark; MAX_OUTLOOK_LINES lines or MAX_BLOCK_CHARS characters (`truncated`).
   Within it, a bullet line, a footnote line (rule 5's marks), a value line, a table row (not
   prose, with a "$" or "%" figure or ending in a number) or a heading line followed by a value
   line or a row (a table's label, "Organic net sales growth(2)" then "49% - 51%") marks the
   block as listed. Before that, up to 2 other lines that aren't headings are taken (the
   sentence that introduces a list) and up to 4 headings of 25 characters or less (column
   headers: "Low", "High", "2026E"); any other heading, or a third such line, ends it. After it,
   anything is taken (column headers, labels, notes) except prose whose first 120 characters
   don't say "expect(s/ed)", "anticipate(s)", "outlook", "guidance", "forecast(s)",
   "project(s/ed)" or "estimate(s/d)". At the end of a block, "Exhibit 99.1" lines and heading
   lines not followed by a value line or a row (a page's running header, a schedule's title) are
   dropped. A block that is only its heading is kept only when the heading states something (30
   characters or more, starting with "Expects", "Raises", "Reaffirms", "Reiterates", "Updates",
   "Lowers", "Narrows", "Maintains", "Affirms", "Provides", "Initiates", "Issues", "Increases",
   "Cuts" or "Withdraws", or their -d/-ed/-ing forms).

Measured on 45 releases sampled across industries (2026-11-08): 33 had an Outlook or Guidance
heading, these rules found 32. No rule reads outlook from words alone ("expects" with a figure):
on the same sample every such line outside those blocks was a CEO quote, a fragment or a side
remark, so a release without an Outlook or Guidance heading gets no outlook and the web app and
the agent say so. Known miss: a release whose text breaks every printed line ("2026 Outlook" / "The" /
"Company now expects ..."), where the block ends at the fragment. Callers quote what this
returns, with its offsets; nothing here reads a figure.
"""

import re
from dataclasses import dataclass

MAX_BLOCK_LINES = 80
MAX_OUTLOOK_LINES = 40
MAX_BLOCK_CHARS = 6000
MAX_NOTES = 6
HEADLINE_LINES = 6

_BULLETS = "•◦▪●*-–"
_PAGE = re.compile(r"^\d{1,3}$")
_SKIP_HEADLINE = re.compile(
    r"^(?:exhibit\s*99|ex-?\s*99|for immediate release|press release|news release|media release"
    r"|contact|[a-z ]{2,25}:\s)", re.I)
_FOOTNOTE = re.compile(r"^(?:\(\d{1,2}\)|\([A-Z]\)|\*{1,3})")
_DOLLARS = re.compile(r"\$\s?\d")
_FIGURE = re.compile(r"[$%]")
_DIGIT = re.compile(r"\d")
_OUTLOOK_WORD = re.compile(r"\b(?:outlook|guidance)\b", re.I)
_NOT_OUTLOOK = re.compile(r"reconcil|non-gaap|forward-looking|safe harbor|cautionary", re.I)
_STATES = re.compile(
    r"^(?:expect|raise|reaffirm|reiterate|update|lower|narrow|maintain|affirm|provide|initiate"
    r"|issue|increase|cut|withdraw)(?:s|d|ed|ing)?\b", re.I)
_RECONCIL = re.compile(r"reconcil", re.I)
_CF_LINE = re.compile(r"^adjustments to reconcile", re.I)
_ADJUSTED_TITLE = re.compile(r"reconcil|non-gaap|adjusted|gaap", re.I)
_COLUMN_HEADER = re.compile(r"months ended|weeks ended|quarters? ended|year ended", re.I)
_YEAR = re.compile(r"^(?:19|20)\d\d$")
_MONTH = re.compile(r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?$", re.I)
_DAY = re.compile(r"^\d{1,2}$")
_DATELINE = re.compile(
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},\s+(?:19|20)\d\d\)?"
    r"\s*(?:--|[-–—―])"
    r"|business\s*wire|pr\s*newswire|globe\s*newswire|accesswire", re.I)
# "MANCHESTER, Conn. -", "SANTA CLARA, Calif.—", "Goleta, California (October 22, 2026) --"
_PLACE = re.compile(r"^[A-Z][A-Za-z .'’]{1,30},\s+[A-Z][A-Za-z.]{1,15}\.?\s*(?:\([^)]{0,40}\)\s*)?"
                    r"(?:--|[-–—―])")
# headings that open another part of a release (rule 6 stops at them)
_SECTION = re.compile(
    r"^(?:(?:business |financial |recent |quarterly |key |operational |other )?highlights|recent "
    r"developments|conference call|earnings call|webcast|about\b|forward-looking|cautionary|safe "
    r"harbor|non-gaap|use of non-gaap|investor|media|contacts?\b|dividends?\b|capital allocation"
    r"|share repurchases?|stock repurchases?|balance sheet|liquidity|cash flows?\b|segment"
    r"|(?:first|second|third|fourth) quarter"
    r"(?! (?:and full[- ]year |fiscal )?\d{4} (?:outlook|guidance))"
    r"|q[1-4]\b|results\b|financial results|summary|cfo commentary|management commentary)", re.I)
_NUMBER_END = re.compile(r"(?:^|\s)[$(]?\d[\d,.]*[%)]?$")
_LATER_PROSE = re.compile(
    r"\b(?:expects?|expected|anticipates?|outlook|guidance|forecasts?|projects?|projected"
    r"|estimates?|estimated)\b", re.I)
_EXHIBIT_LINE = re.compile(r"^(?:exhibit|ex-)\s*99", re.I)
_STATEMENTS_START = re.compile(
    r"^(?:condensed consolidated|consolidated statements?|consolidated balance|unaudited condensed"
    r"|statements? of operations|forward-looking statements)", re.I)


@dataclass(frozen=True)
class Line:
    text: str
    start: int  # offsets into the text
    end: int


@dataclass(frozen=True)
class Passage:
    kind: str  # "headline", "outlook", "reconciliation", "note"
    heading: str | None
    text: str
    start: int  # offsets into the release's text
    end: int
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "heading": self.heading, "text": self.text,
                "char_start": self.start, "char_end": self.end, "truncated": self.truncated}


@dataclass(frozen=True)
class Reading:
    headline: Passage | None
    outlook: list[Passage]
    adjustments: list[Passage]  # reconciliation tables, then notes; text order within each


def read_release(text: str) -> Reading:
    """Rules 1-6 over one release's text."""
    lines = lines_of(text)
    tables = _reconciliations(lines)
    taken = {k for _, a, b in tables for k in range(a, b + 1)}
    found = _outlook(lines, taken)
    quoted = taken | {k for _, a, b in found for k in range(a, b + 1)}
    return Reading(headline(lines), [p for p, _, _ in found],
                   [p for p, _, _ in tables] + _notes(lines, quoted))


def lines_of(text: str) -> list[Line]:
    """Rule 1."""
    out, at = [], 0
    for raw in text.split("\n"):
        t = raw.strip()
        if t and not _PAGE.match(t):
            lead = len(raw) - len(raw.lstrip())
            out.append(Line(t, at + lead, at + lead + len(t)))
        at += len(raw) + 1
    return out


def is_heading(s: str, max_len: int = 160, colon: bool = False) -> bool:
    """Rule 2's heading line (colon: one ending in ":" too)."""
    if not 3 <= len(s) <= max_len or "$" in s or "%" in s or s[0] in _BULLETS or s[0] == "(":
        return False
    if s[-1] in (".,;" if colon else ".,;:") or len(s.split()) > 20:
        return False
    chars = [c for c in s if not c.isspace()]
    return sum(c.isalpha() for c in chars) * 2 >= len(chars)


def is_value(s: str) -> bool:
    """Rule 2's value line."""
    return sum(c.isalpha() for c in s) <= 3 and bool(_DIGIT.search(s))


def is_prose(s: str) -> bool:
    return len(s) >= 120 and s[-1] in ".)”\""


def is_dateline(s: str) -> bool:
    return bool(_DATELINE.search(s[:120]) or _PLACE.match(s))


def dates_only(s: str) -> bool:
    """A column header of years and dates only ("2026 2025", "July 26, April 26,"): every word a
    year, a month or a day of the month, and at least one a year or a month."""
    words = [w for w in re.split(r"[\s,]+", s) if w]
    return (bool(words) and all(_YEAR.match(w) or _MONTH.match(w) or _DAY.match(w) for w in words)
            and any(_YEAR.match(w) or _MONTH.match(w) for w in words))


def headline(lines: list[Line]) -> Passage | None:
    """Rule 3."""
    for ln in lines[:HEADLINE_LINES]:
        if 20 <= len(ln.text) <= 300 and not _SKIP_HEADLINE.match(ln.text):
            return Passage("headline", None, ln.text, ln.start, ln.end)
    return None


def _statements_start(lines: list[Line]) -> int:
    for i, ln in enumerate(lines):
        if _STATEMENTS_START.match(ln.text):
            return i
    return len(lines)


def _block(lines: list[Line], first: int, last: int, kind: str, truncated: bool) -> Passage:
    body = lines[first:last + 1]
    return Passage(kind, body[0].text, "\n".join(ln.text for ln in body), body[0].start,
                   body[-1].end, truncated)


def _too_long(lines: list[Line], first: int, i: int) -> bool:
    return i - first + 1 > MAX_BLOCK_LINES or lines[i].end - lines[first].start > MAX_BLOCK_CHARS


def _is_reconciliation_heading(s: str) -> bool:
    return is_heading(s, 200) and bool(_RECONCIL.search(s)) and not _CF_LINE.match(s)


def _reconciliations(lines: list[Line]) -> list[tuple[Passage, int, int]]:
    """Rule 4: (block, first line, last line)."""
    out = []
    i = 0
    while i < len(lines):
        if not _is_reconciliation_heading(lines[i].text):
            i += 1
            continue
        first, j, truncated = i, i + 1, False
        while j < len(lines):
            s = lines[j].text
            nxt = lines[j + 1].text if j + 1 < len(lines) else ""
            if _is_reconciliation_heading(s):
                break
            if is_heading(s) and is_prose(nxt) and not _FOOTNOTE.match(nxt):
                break
            if (is_heading(s) and not _ADJUSTED_TITLE.search(s) and not _COLUMN_HEADER.search(s)
                    and (_COLUMN_HEADER.search(nxt) or dates_only(nxt))):
                break
            if _too_long(lines, first, j):
                truncated = True
                break
            j += 1
        last = j - 1
        while last > first and is_heading(lines[last].text) and not (
                last + 1 < len(lines) and is_value(lines[last + 1].text)):
            last -= 1
        out.append((_block(lines, first, last, "reconciliation", truncated), first, last))
        i = j
    return out


def _notes(lines: list[Line], taken: set[int]) -> list[Passage]:
    """Rule 5."""
    out = []
    for i, ln in enumerate(lines[:_statements_start(lines)]):
        if (i not in taken and len(ln.text) >= 80 and _FOOTNOTE.match(ln.text)
                and _DOLLARS.search(ln.text)):
            out.append(Passage("note", None, ln.text, ln.start, ln.end))
            if len(out) == MAX_NOTES:
                break
    return out


def _is_outlook_heading(s: str, colon: bool = False) -> bool:
    return (is_heading(s, 100, colon) and (s[0].isupper() or s[0].isdigit()) and ". " not in s
            and bool(_OUTLOOK_WORD.search(s)) and not _NOT_OUTLOOK.search(s))


def _is_row(s: str) -> bool:
    """A table row (rule 6): not prose, and a "$" or "%" figure or a number at the end."""
    if is_prose(s):
        return False
    return bool(_FIGURE.search(s) and _DIGIT.search(s)) or bool(_NUMBER_END.search(s))


def _outlook(lines: list[Line], taken: set[int]) -> list[tuple[Passage, int, int]]:
    """Rule 6: (block, first line, last line)."""
    out = []
    i = 0
    while i < len(lines):
        if i in taken or not _is_outlook_heading(lines[i].text, colon=True):
            i += 1
            continue
        first, j, truncated = i, i + 1, False
        listed = False  # a bullet, footnote, value or table line seen in this block
        early = columns = 0
        while j < len(lines) and j not in taken:
            s = lines[j].text
            nxt = lines[j + 1].text if j + 1 < len(lines) else ""
            heading = is_heading(s)
            if _is_outlook_heading(s) or is_dateline(s) or (heading and _SECTION.match(s)):
                break
            if heading and is_prose(nxt) and not _FOOTNOTE.match(nxt):
                break
            if (j - first + 1 > MAX_OUTLOOK_LINES
                    or lines[j].end - lines[first].start > MAX_BLOCK_CHARS):
                truncated = True
                break
            if s[0] in "•◦▪●" or _FOOTNOTE.match(s) or is_value(s) or _is_row(s):
                listed = True
            elif heading and (is_value(nxt) or _is_row(nxt)):
                listed = True
            elif not listed:
                if heading and len(s) <= 25 and columns < 4:  # "Low", "High", "2026E"
                    columns += 1
                elif heading or early == 2:
                    break
                else:
                    early += 1
            elif is_prose(s) and not _LATER_PROSE.search(s[:120]):
                break
            j += 1
        last = j - 1
        while last > first and (_EXHIBIT_LINE.match(lines[last].text) or (  # page furniture
                is_heading(lines[last].text) and not (
                    last + 1 < len(lines) and (is_value(lines[last + 1].text)
                                               or _is_row(lines[last + 1].text))))):
            last -= 1
        title = lines[first].text
        if last > first or (len(title) >= 30 and _STATES.match(title)):
            out.append((_block(lines, first, last, "outlook", truncated), first, last))
        i = max(j, first + 1)
    return out
