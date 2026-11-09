"""KPIs a company states in its filings' words but doesn't tag in XBRL (H11): its backlog, its
orders for a quarter and their growth, read from MD&A (10-K Item 7, 10-Q Part I Item 2), the
10-K's Item 1 and the earnings release (the EX-99.1 of an 8-K with Item 2.02) by fixed rules, no
model. Pure: the route reads the sections under RLS and passes them in.

Precision before recall: a figure is kept only when its sentence or table row says, in one of
the standard ways below, what it is, for the whole company, at which date. Everything else is
left out, and the web app says a company may state these figures in ways not read here.

Rules (constants below):

1. Passages. A section's text is split into lines ("\\n", the parser's block elements) and each
   line into sentences: after ".", "!", "?" or ";" followed by a space and a capital, a digit, a
   bullet, a quote or "(", unless the word before is an abbreviation ("Dec.", "U.S.", "approx.").
   A passage's offsets are into the section's text; a citation adds the section's char_start.
2. Amounts. In a sentence: "$" + a number + a scale word ("$15.0 billion", "$9.7B", "$59,828
   million"); a "$" number without a scale word is never read. In a table row: a number read at
   the scale its table states (rule 6). Figures are dollars: nothing in another currency is read.
3. The subject. The words right before the keyword ("backlog", "orders"), back to the start of
   its clause (a comma, ";", ":", "(", "and", "while" or a bullet; a date's comma doesn't end it),
   must all be in the metric's word list (BACKLOG_WORDS, ORDER_WORDS, GROWTH_WORDS), a year, a
   full date or the company's own name ("Vertiv's estimated combined order backlog", "the dollar
   amount of backlog", "GE Vernova orders"). A subject with "total", "company", "consolidated",
   "combined", "overall" or the company's name is strong (rule 9). A word of a segment before the
   figure ("segment", "division", "business unit"), a segment's name ("Aeronautics backlog",
   "Backlog at Defense, Space & Security", "Defense Backlog and Bookings – Backlog was") or a
   kind of backlog (funded, unfunded, contractual, unobligated, services, product) fails it.
4. Backlog in a sentence: a subject (rule 3); then only a period phrase ("at the end of the third
   quarter of 2021", "as of December 31, 2025") or "believed to be firm"; a verb of being ("was",
   "of", "totaled", "reached", "increased to", "increased 11% to", "remains robust at"); at most
   "approximately", "about", "~" or "a record"; the amount. A bound ("over", "more than",
   "nearly"), a part ("of the total backlog ... approximately $3.9 billion was not expected to be
   filled", "38% of our backlog") and a word of expectation before the amount (FUTURE: guidance)
   are never read.
   Dates: "as of" / "at" + a full date after the amount ("$23.1 billion at December 31, 2021"),
   before the subject ("At December 31, 2021, our backlog was") or in it ("December 31, 2024
   backlog of $5,070 million"); two amounts take two dates ("$15.0 billion and $7.2 billion as of
   December 31, 2025 and 2024, respectively"; "$135.4 billion compared with $147.1 billion at
   December 31, 2020"). Without a full date the figure is as of the document's period end (rule
   7), only when the words up to the amount name no other year (outside a comparison: "compared
   to fourth quarter 2021") and nothing after it dates it otherwise ("as of the end of August").
5. Orders for a quarter: a strong subject or "our" (rule 3; "Orders of $5.0 billion" under a
   segment's heading has none), a verb ("of", "were", "totaled", "increased 25% to"), the amount;
   the sentence names the quarter ("quarter", "three months", "Q3") and no longer span
   (SPAN_WORDS), and the document is a 10-Q or an earnings release (a 10-K's orders are a year's).
   Order growth for a quarter: a subject of GROWTH_WORDS ("third quarter 2025 organic orders"),
   then "up", "increased", "grew", "rose", "down", "declined" (and -ing forms) or "growth of", at
   most "approximately", "about" or "~", a percentage, and within GROWTH_REACH characters a
   year-on-year phrase (YOY: "compared to prior year period", "year-over-year", "vs. prior year",
   "from last year's third quarter") with nothing sequential, trailing-twelve-month or about
   backlog before it. `basis` is "organic" when "organic" or "(excluding foreign exchange)"
   qualifies it, else "reported". The sentence names the quarter.
6. Tables. A row that starts "Total backlog", "Total order backlog" or "Total orders" (any case)
   and then a number: that number is the company's figure in the table's first column, at the
   document's period end, when the lines back to the table's title (a line starting "Backlog",
   "Total backlog", "Orders" or "Total orders", at most TABLE_REACH lines up), or NOTE_REACH lines
   before it, state one scale ("(Dollars in millions)", "in millions"; two different ones: not
   read) in dollars ("Dollars", "$", or a "$" on the number); the first year in the header's short
   lines is the period end's year (Emerson's "2024 2025" puts last year first: not read); and the
   header doesn't split it into funded and unfunded (Northrop's first column is funded backlog).
   "Total orders" needs "Three months ended" in the header. Rows without "Total" ("Backlog $ 9,698
   $ 7,555 28 %", a segment table's line) are never read.
7. Periods. A 10-K's or 10-Q's period end is its period of report; an earnings release's is the
   quarter it reports (the caller's: query_api.events' periods and assign_releases). Backlog is
   an instant at that date, orders and order growth are that quarter's.
8. Verbatim. A figure is kept only when the number as written ("15.0", "635,688", "60") is in its
   passage, the way verify.py quotes: the cited passage shows the figure.
9. One value per metric, date and basis. Figures within each other's rounding agree ("$135.4
   billion" and "135,355" in millions). When they disagree, the figure with the strongest source
   wins (a table's total row, then a strong subject, then any other); between equals, the one
   more filings state; otherwise the date is left out. `conflicts` lists every disagreement and
   what was kept (Boeing's 10-K: the defense segment's "Total backlog of $59,828 million at
   December 31, 2021" loses to the table's $377,499 million). The kept value is the most precise;
   every passage that states it is cited, newest filing first.

Measured by hand on 2026-11-09 (README "Backlog and orders from the text"): every kept value of
21 companies' 2021-2026 MD&A, Item 1 and earnings releases checked against its passage.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query

from query_api.events import assign_releases, items_of, periods
from query_api.store import Store

CompanyId = Annotated[str, Path(pattern=r"^\d{10}$", description="canonical company_id")]

METHOD = ("Backlog and orders as the filings' own words state them, read by fixed rules from MD&A, "
          "the 10-K's Item 1 and earnings releases (query_api/text_kpis.py): each value cites its "
          "passage and is kept only when its number is in that passage. A company may state them "
          "in ways these rules don't read; nothing is estimated or computed.")

METRICS = ("backlog", "orders", "orders_growth")
TABLE_REACH = 14
NOTE_REACH = 12
GROWTH_REACH = 90
HEADER_LINE = 60  # a header line is short; a longer one is prose (its years aren't columns)
SCALES = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
          "thousand": 1e3, "k": 1e3}
STRENGTH = {"table": 3, "strong": 2, "plain": 1}

_ABBREV = re.compile(
    r"(?:\b(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|inc|corp|co|ltd|no|nos|approx"
    r"|vs|mr|ms|dr|st|e\.g|i\.e|u\.s|u\.k|l\.p|n\.a)|\b[a-z])\.$", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9•“\"(])")
_NUMBER = r"(?P<n>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<f>\d+))?"
AMOUNT = re.compile(
    rf"\$\s?{_NUMBER}\s?(?P<u>billion|million|thousand|bn|mn|[BMK])(?![A-Za-z])", re.I)
_MONTH = (r"(?:jan(?:uary|\.)?|feb(?:ruary|\.)?|mar(?:ch|\.)?|apr(?:il|\.)?|may|june?|july?"
          r"|aug(?:ust|\.)?|sept?(?:ember|\.)?|oct(?:ober|\.)?|nov(?:ember|\.)?|dec(?:ember|\.)?)")
DATE = re.compile(rf"\b(?P<m>{_MONTH})\s+(?P<d>\d{{1,2}}),?\s+(?P<y>(?:19|20)\d\d)\b", re.I)
# the same two without named groups, to compose (a match is read again with AMOUNT or DATE)
_AMOUNT_RAW = (r"\$\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?(?:billion|million|thousand|bn|mn"
               r"|[BMK])(?![A-Za-z])")
_DATE_RAW = rf"\b{_MONTH}\s+\d{{1,2}},?\s+(?:19|20)\d\d\b"
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_YEAR = re.compile(r"\b(?:19|20)\d\d\b")
# a year after these is the comparison's, not the figure's
_COMPARED = re.compile(r"\b(?:compared|versus|vs\.?|from|than|over|since|prior|last)\b[^.;]{0,40}$",
                       re.I)

BACKLOG_WORDS = frozenset({
    "total", "our", "the", "its", "company", "consolidated", "combined", "overall", "estimated",
    "order", "firm", "record", "ending", "year-end", "quarter-end", "dollar", "amount", "of", "a",
    "an", "current", "increased", "grew", "built", "with", "strong", "robust"})
ORDER_WORDS = frozenset({"total", "our", "the", "its", "company", "consolidated", "quarterly",
                         "in", "quarter"})
GROWTH_WORDS = ORDER_WORDS | frozenset({
    "organic", "first", "second", "third", "fourth", "fiscal", "q1", "q2", "q3", "q4", "strong"})
STRONG_WORDS = frozenset({"total", "company", "consolidated", "combined", "overall"})
# allowed only beside a full date in the subject
_COMPARING = frozenset({"as", "compared", "to", "with", "versus", "vs", "from"})
_NAME_STOP = frozenset({"holdings", "holding", "co", "corp", "corporation", "inc", "plc", "ltd",
                        "limited", "company", "group", "the", "n.v.", "nv", "sa", "ag", "se", "&",
                        "and", "de", "intl", "international", "of", "/de/", "de/"})
# where a subject's clause starts (a date's own comma is masked first)
_CLAUSE = re.compile(r"[,;:(]|\band\b(?!\s+[$\d])|\bwhile\b|•", re.I)
# orders' subjects also start after "with" ("..., with third quarter organic orders up 60%"); a
# backlog's doesn't ("The Aerospace segment ended the quarter with backlog of $22.3 billion")
_ORDERS_CLAUSE = re.compile(r"[,;:(]|\band\b(?!\s+[$\d])|\bwhile\b|\bwith\b|•", re.I)
_SEGMENT = re.compile(r"\b(?:segments?|divisions?|business\s+units?|reportable)\b", re.I)
_PERIOD_PHRASE = (r"at\s+(?:the\s+)?end\s+of\s+(?:the\s+)?(?:fiscal\s+)?(?:(?:first|second|third"
                  r"|fourth)\s+)?(?:quarter|year|period)(?:\s+of\s+(?:fiscal\s+)?(?:19|20)\d\d)?"
                  r"|at\s+(?:fiscal\s+)?(?:year|quarter)[- ]end(?:\s+(?:19|20)\d\d)?")
MID = re.compile(rf"^\s*,?\s*(?:(?:(?:as\s+of|at|on)\s+(?P<date>{_DATE_RAW})|{_PERIOD_PHRASE}"
                 r"|believed\s+to\s+be\s+firm)\s*,?\s*){0,2}", re.I)
# not "were": "Our remaining performance obligations and backlog were $14.37 billion and $31.31
# billion" lists two figures and the first isn't the backlog
BEING = re.compile(
    r"^(?:was|is|of|totaled|totalled|amounted\s+to|reached|stood\s+at|ended\s+(?:the\s+"
    r"(?:quarter|year)\s+)?at|remained\s+at|remains\s+(?:\w+\s+)?at|(?:increased|grew|rose|climbed"
    r"|decreased|declined|fell)(?:\s+(?:by\s+)?\d+(?:\.\d+)?\s?%)?\s+to)\s+", re.I)
QUALIFIER = re.compile(r"^(?:approximately|about|~|a\s+record(?:\s+high\s+of)?|record|an\s+all-time"
                       r"\s+high\s+of)\s*", re.I)
# after an amount: a date phrase, and the ones that mean the document's own period
_TAIL_DATE = re.compile(r"^\s*,?\s*(?:as\s+of|at|on)\b", re.I)
_TAIL_PERIOD = re.compile(
    r"^\s*,?\s*(?:as\s+of|at)\s+(?:the\s+)?(?:end\s+of\s+(?:the\s+)?(?:fiscal\s+)?(?:(?:first"
    r"|second|third|fourth)\s+)?(?:quarter|period|year)\b(?!\s+(?:of\s+)?(?:19|20)\d\d)"
    r"|(?:fiscal\s+)?(?:quarter|year)[- ]end\b(?!\s+(?:19|20)\d\d))", re.I)
_BOUND = re.compile(r"(?:over|more\s+than|nearly|almost|exceed(?:ed|ing)?|above|below|less\s+than"
                    r"|up\s+to|at\s+least)\s*$", re.I)
FUTURE = re.compile(r"\b(?:expects?|expected|anticipates?|anticipated|will|would|could|may|might"
                    r"|targets?|outlook|guidance|forecasts?|projects?|projected|plans?|aims?)\b",
                    re.I)
NOT_BACKLOG = re.compile(r"\b(?:funded|unfunded|contractual|unobligated|services?|product)\s+"
                         r"backlog\b|\bbacklog\s+(?:of\s+)?(?:units|homes|aircraft|airplanes"
                         r"|orders\s+and)\b", re.I)
QUARTER = re.compile(r"\b(?:quarter|three\s+months|q[1-4])\b", re.I)
SPAN_WORDS = re.compile(r"\b(?:full[- ]year|for\s+the\s+year|fiscal\s+year|year[- ]to[- ]date|"
                        r"nine\s+months|six\s+months|first\s+half|second\s+half|trailing|ttm|"
                        r"twelve\s+months|annual)\b", re.I)
ORDERS_VERB = re.compile(
    r"^(?:were|was|of|totaled|totalled|amounted\s+to|reached|(?:increased|grew|rose|decreased"
    r"|declined|fell)(?:\s+(?:by\s+)?\d+(?:\.\d+)?\s?%)?\s+to)\s+", re.I)
GROWTH = re.compile(
    r"^\s*(?:\((?:excluding|excl\.)[^)]{0,40}\)\s*)?(?:were\s+|was\s+)?"
    r"(?P<dir>up|increased|increasing|grew|growing|rose|rising|down|decreased|decreasing|declined"
    r"|declining|fell|growth\s+of)\s+(?:by\s+)?(?:approximately\s+|about\s+|~\s?)?"
    r"(?P<pct>\(?\d+(?:\.\d+)?\)?)\s?(?:%|percent\b)", re.I)
YOY = re.compile(
    r"compared\s+(?:to|with)\s+(?:the\s+)?(?:prior|last|previous)[- ]year|year[- ]over[- ]year|"
    r"year[- ]on[- ]year|vs\.?\s+(?:the\s+)?prior[- ]year|versus\s+(?:the\s+)?prior[- ]year|"
    r"from\s+(?:the\s+)?(?:prior|last)[- ]year|compared\s+(?:to|with)\s+(?:the\s+)?(?:same\s+"
    r"(?:quarter|period)\s+(?:of\s+)?last\s+year|last\s+year[’']s\s+(?:first|second|third|fourth)"
    r"\s+quarter)", re.I)
NOT_GROWTH = re.compile(r"sequential|trailing|\bttm\b|backlog|book-to-bill", re.I)
ORGANIC = re.compile(r"\borganic|\(excluding\s+foreign\s+exchange\)", re.I)
TABLE_ROW = re.compile(r"^\s*total\s+(?P<what>order\s+backlog|backlog|orders)\b(?P<rest>.*)$", re.I)
TABLE_TITLE = re.compile(r"^\s*(?:total\s+)?(?:order\s+)?(?:backlog|orders)\b", re.I)
TABLE_NUMBER = re.compile(rf"(?P<dollar>\$)?\s?{_NUMBER}")
SCALE_NOTE = re.compile(r"\bin\s+(?P<s>thousands|millions|billions)\b", re.I)
TABLE_SCALES = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}
THREE_MONTHS = re.compile(r"three\s+months\s+ended|quarter\s+ended", re.I)
FUNDED = re.compile(r"\b(?:un)?funded\b", re.I)


@dataclass(frozen=True)
class Doc:
    """One section of one filing, as the route read it."""
    accession_no: str
    form: str
    filed: date
    section_key: str
    period_end: date | None  # rule 7: the 10-K/10-Q's period, or the quarter a release reports
    text: str
    char_start: int = 0  # the section's offset in the document (citations)


@dataclass(frozen=True)
class Found:
    metric: str
    period_end: date
    value: float  # dollars, or percentage points for orders_growth
    precision: float  # the rounding step of what's written, in the value's units
    written: str  # the number as the passage writes it ("15.0", "635,688", "60")
    how: str  # sentence | table
    strength: str  # table | strong | plain (rule 9)
    accession_no: str
    form: str
    filed: date
    section_key: str
    start: int  # the passage, in the section's text
    end: int
    passage: str
    char_start: int = 0
    basis: str | None = None  # orders_growth: organic | reported

    def citation(self) -> dict:
        return {"accession_no": self.accession_no, "form": self.form, "filed": self.filed,
                "section_key": self.section_key, "char_start": self.char_start + self.start,
                "char_end": self.char_start + self.end}


@dataclass
class Series:
    metric: str
    points: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class _Subject:
    strength: str
    when: date | None  # a full date in the subject ("December 31, 2024 backlog")
    words: tuple[str, ...]


# --- passages -------------------------------------------------------------------------------

def _lines(text: str) -> list[tuple[int, int]]:
    """Rule 1's lines, with a line that goes on in lower case joined to the one before it when
    that one doesn't end a sentence (a block element broken mid-sentence: "... order growth
    and" / "record backlog of $2.4 billion.")."""
    spans: list[tuple[int, int]] = []
    at = 0
    for line in text.split("\n"):
        start, end = at, at + len(line)
        if spans and line[:1].islower() and not re.search(r"[.!?:;]\s*$", text[spans[-1][0]:
                                                                               spans[-1][1]]):
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
        at = end + 1
    return spans


_KEYWORD = re.compile(r"backlog|orders", re.I)


def sentences(text: str, only: re.Pattern | None = None) -> list[tuple[int, int, str]]:
    """(start, end, sentence) in text order (rule 1); with `only`, of the lines it matches."""
    out = []
    for at, end in _lines(text):
        line = text[at:end]
        if only is not None and not only.search(line):
            continue
        cut = at
        for m in _SENTENCE_END.finditer(line):
            if _ABBREV.search(line[: m.start()]):
                continue
            out.append((cut, at + m.start(), text[cut:at + m.start()]))
            cut = at + m.end()
        if end > cut:
            out.append((cut, end, text[cut:end]))
    return [(a, b, t) for a, b, t in out if t.strip()]


def name_words(name: str | None) -> frozenset[str]:
    """The company's own name as words a subject may use ("Vertiv's", "GE Vernova")."""
    words = set()
    for w in re.findall(r"[A-Za-z][A-Za-z.&'’/-]*", name or ""):
        w = w.lower().rstrip(".")
        if w and w not in _NAME_STOP and len(w) > 1:
            words.add(w)
    return frozenset(words)


def _word(w: str) -> str:
    return re.sub(r"[’']s$", "", w.lower().strip(".,"))


def _date(m: re.Match | None, year: str | None = None) -> date | None:
    if m is None:
        return None
    month = _MONTHS.get(m.group("m").lower().rstrip(".")[:3])
    try:
        return date(int(year or m.group("y")), month or 0, int(m.group("d")))
    except (ValueError, TypeError):
        return None


def _subject(before: str, allowed: frozenset[str], names: frozenset[str],
             clauses: re.Pattern = _CLAUSE) -> _Subject | None:
    """Rule 3: the subject of the keyword, or None when a word in it isn't allowed."""
    dates = list(DATE.finditer(before))
    masked = before
    for i, m in reversed(list(enumerate(dates))):
        masked = masked[: m.start()] + f" DATE{i} " + masked[m.end():]
    clause = re.split(clauses, masked)[-1]
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.&'’/-]*", clause)
    if len(words) > 8:
        return None
    when, strong = None, False
    dated = any(re.fullmatch(r"DATE\d+", w) for w in words)
    for w in words:
        key = _word(w)
        if dated and key in _COMPARING:
            continue  # "as compared to December 31, 2024 backlog of": dated, so no guess
        if re.fullmatch(r"DATE\d+", w):
            when = _date(dates[int(w[4:])])
            continue
        if key in names or w.lower().rstrip(".,") in names:
            strong = True
            continue
        if key in allowed or _YEAR.fullmatch(key):
            strong = strong or key in STRONG_WORDS
            continue
        return None
    return _Subject("strong" if strong else "plain", when, tuple(_word(w) for w in words))


def _amount(m: re.Match) -> tuple[float, float, str]:
    whole, frac, unit = m.group("n"), m.group("f"), m.group("u")
    scale = SCALES[unit.lower()]
    decimals = len(frac) if frac else 0
    written = whole + (f".{frac}" if frac else "")
    return float(written.replace(",", "")) * scale, scale * 10 ** -decimals, written


def _other_year(words: str, period_end: date) -> bool:
    """The words name a year that isn't the period's, outside a comparison (rule 4). A fiscal
    year named for the year it ends ("third quarter of fiscal 2027", ending in October 2026) is
    the period's."""
    for m in _YEAR.finditer(words):
        year = int(m.group(0))
        if year == period_end.year:
            continue
        if year == period_end.year + 1 and re.search(r"\bfiscal\s+(?:year\s+)?$",
                                                     words[: m.start()], re.I):
            continue
        if _COMPARED.search(words[: m.start()]):
            continue
        return True
    return False


def _found(doc: Doc, metric: str, when: date, value: float, step: float, written: str, how: str,
           strength: str, s0: int, s1: int, passage: str, basis: str | None = None) -> Found:
    return Found(metric, when, value, step, written, how, strength, doc.accession_no, doc.form,
                 doc.filed, doc.section_key, s0, s1, passage, doc.char_start, basis)


# --- rules 3-5: sentences -------------------------------------------------------------------

def _backlog(doc: Doc, s0: int, s1: int, sent: str, names: frozenset[str]) -> list[Found]:
    if NOT_BACKLOG.search(sent):
        return []
    for k in re.finditer(r"\bbacklog\b", sent, re.I):
        before, after = sent[: k.start()], sent[k.end():]
        lead = re.match(rf"^\s*(?:•\s*)?(?:at|as\s+of)\s+(?P<date>{_DATE_RAW})\s*,\s*", before,
                        re.I)
        subject = _subject(before[lead.end():] if lead else before, BACKLOG_WORDS, names)
        if subject is None:
            continue
        mid = MID.match(after)
        rest = after[mid.end():] if mid else after
        verb = BEING.match(rest.lstrip())
        if not verb:
            continue
        rest = rest.lstrip()[verb.end():]
        qual = QUALIFIER.match(rest)
        rest = rest[qual.end():] if qual else rest
        first = AMOUNT.match(rest)
        if not first:
            continue
        amt_at = len(sent) - len(rest)
        if FUTURE.search(sent[:amt_at]) or _BOUND.search(sent[:amt_at]) or \
                _SEGMENT.search(sent[:amt_at]):
            return []
        values: list[re.Match | None] = [first]
        tail = rest[first.end():]
        two = re.match(rf"^\s*,?\s*and\s+(?P<a>{_AMOUNT_RAW})\s*,?\s*(?:as\s+of|at)\s+"
                       rf"(?P<d1>{_DATE_RAW})\s+and\s+(?:(?P<d2>{_DATE_RAW})"
                       r"|(?P<y2>(?:19|20)\d\d))", tail, re.I)
        own = re.match(rf"^\s*,?\s*(?:as\s+of|at)\s+(?P<d>{_DATE_RAW})", tail, re.I)
        compared = (rf"^\s*,?\s*(?:and|compared\s+(?:with|to))\s+(?P<a>{_AMOUNT_RAW})\s*,?\s*"
                    rf"(?:as\s+of|at)\s+(?P<d>{_DATE_RAW})")
        dates: list[date | None]
        if two:
            values.append(AMOUNT.match(two.group("a")))
            d1 = DATE.match(two.group("d1"))
            dates = [_date(d1), _date(DATE.match(two.group("d2"))) if two.group("d2")
                     else _date(d1, two.group("y2"))]
        elif own:
            dates = [_date(DATE.match(own.group("d")))]
            more = re.match(compared, tail[own.end():], re.I)
            if more:
                values.append(AMOUNT.match(more.group("a")))
                dates.append(_date(DATE.match(more.group("d"))))
        else:
            known = (_date(DATE.match(lead.group("date"))) if lead else None) or subject.when \
                or (_date(DATE.match(mid.group("date"))) if mid and mid.group("date") else None)
            dates = [known]
            more = re.match(compared, tail, re.I)
            if more and known:
                values.append(AMOUNT.match(more.group("a")))
                dates.append(_date(DATE.match(more.group("d"))))
            if known is None:
                # the document's period (rule 4)
                if doc.period_end is None or _other_year(sent[:amt_at], doc.period_end):
                    return []
                if _TAIL_DATE.match(tail) and not _TAIL_PERIOD.match(tail):
                    return []
                dates = [doc.period_end]
        out = []
        for m, when in zip(values, dates, strict=False):
            if m is None or when is None:
                continue
            value, step, written = _amount(m)
            out.append(_found(doc, "backlog", when, value, step, written, "sentence",
                              subject.strength, s0, s1, sent))
        return out  # one subject per sentence
    return []


def _orders(doc: Doc, s0: int, s1: int, sent: str, names: frozenset[str]) -> list[Found]:
    if doc.form not in ("10-Q", "10-Q/A", "8-K") or doc.period_end is None:
        return []
    if not QUARTER.search(sent) or SPAN_WORDS.search(sent):
        return []
    for k in re.finditer(r"\borders\b", sent, re.I):
        before, after = sent[: k.start()], sent[k.end():]
        lead = re.match(r"^\s*(?:•\s*)?in\s+the\s+(?:(?:first|second|third|fourth)\s+)?quarter\s*,"
                        r"\s*", before, re.I)
        subject = _subject(before[lead.end():] if lead else before, ORDER_WORDS, names,
                           _ORDERS_CLAUSE)
        if subject is None or not (subject.strength == "strong" or "our" in subject.words):
            continue
        verb = ORDERS_VERB.match(after.lstrip())
        if not verb:
            continue
        rest = after.lstrip()[verb.end():]
        qual = QUALIFIER.match(rest)
        rest = rest[qual.end():] if qual else rest
        m = AMOUNT.match(rest)
        if not m:
            continue
        upto = len(sent) - len(rest) + m.end()
        if FUTURE.search(sent[:upto]) or _SEGMENT.search(sent[:upto]) or \
                _other_year(sent[:upto], doc.period_end):
            return []
        value, step, written = _amount(m)
        return [_found(doc, "orders", doc.period_end, value, step, written, "sentence",
                       subject.strength, s0, s1, sent)]
    return []


def _growth(doc: Doc, s0: int, s1: int, sent: str, names: frozenset[str]) -> list[Found]:
    if doc.form not in ("10-Q", "10-Q/A", "8-K") or doc.period_end is None:
        return []
    if not QUARTER.search(sent):
        return []
    for k in re.finditer(r"\borders\b", sent, re.I):
        before, after = re.sub(r"^\s*•\s*", "", sent[: k.start()]), sent[k.end():]
        subject = _subject(before, GROWTH_WORDS, names, _ORDERS_CLAUSE)
        if subject is None or not [w for w in subject.words if w not in ("the", "in", "our")]:
            continue  # "Orders grew 19%" in a segment's paragraph: whose orders isn't said
        g = GROWTH.match(after)
        if not g:
            continue
        clause = " ".join(subject.words)
        upto = k.end() + g.end()
        if SPAN_WORDS.search(clause) or SPAN_WORDS.search(after[: g.end()]) or \
                _SEGMENT.search(sent[:upto]) or FUTURE.search(sent[:upto]) or \
                _other_year(sent[:upto], doc.period_end):
            continue
        reach = after[g.end(): g.end() + GROWTH_REACH]
        y = YOY.search(reach)
        if not y or NOT_GROWTH.search(reach[: y.start()]) or ";" in reach[: y.start()]:
            continue
        pct = g.group("pct")
        negative = pct.startswith("(") or g.group("dir").lower().startswith(
            ("down", "decreas", "declin", "fell"))
        written = pct.strip("()")
        value = float(written) * (-1 if negative else 1)
        decimals = len(written.split(".")[1]) if "." in written else 0
        basis = "organic" if ORGANIC.search(clause + " " + after[: g.end() + 40]) else "reported"
        return [_found(doc, "orders_growth", doc.period_end, value, 10 ** -decimals, written,
                       "sentence", subject.strength, s0, s1, sent, basis=basis)]
    return []


# --- rule 6: tables ------------------------------------------------------------------------

def _tables(doc: Doc) -> list[Found]:
    if doc.period_end is None:
        return []
    lines = doc.text.split("\n")
    starts, at = [], 0
    for line in lines:
        starts.append(at)
        at += len(line) + 1
    out = []
    for i, line in enumerate(lines):
        row = TABLE_ROW.match(line)
        if not row:
            continue
        first = TABLE_NUMBER.search(row.group("rest"))
        if not first or row.group("rest")[: first.start()].strip(" $:"):
            continue
        title = next((j for j in range(i - 1, max(-1, i - TABLE_REACH), -1)
                      if TABLE_TITLE.match(lines[j]) and not TABLE_ROW.match(lines[j])), None)
        if title is None and SCALE_NOTE.search(line):
            title = i  # "Total backlog (Dollars in millions)" is its own title (Boeing's release)
        if title is None:
            continue
        what = row.group("what").lower()
        header = lines[title:i]
        around = "\n".join(lines[max(0, title - NOTE_REACH): i] + [line])
        notes = {m.group("s").lower() for m in SCALE_NOTE.finditer(around)}
        note = SCALE_NOTE.search(line) or SCALE_NOTE.search(around)
        if not note or len(notes) > 1:
            continue
        if not (first.group("dollar") or re.search(r"dollars|\$", around, re.I)):
            continue
        if FUNDED.search(around):
            continue
        years = [y for h in header if len(h) <= HEADER_LINE for y in _YEAR.findall(h)]
        if not years or int(years[0]) != doc.period_end.year:
            continue
        if what == "orders" and not THREE_MONTHS.search(around):
            continue
        whole, frac = first.group("n"), first.group("f")
        written = whole + (f".{frac}" if frac else "")
        scale = TABLE_SCALES[note.group("s").lower()]
        decimals = len(frac) if frac else 0
        out.append(_found(doc, "orders" if what == "orders" else "backlog", doc.period_end,
                          float(written.replace(",", "")) * scale, scale * 10 ** -decimals,
                          written, "table", "table", starts[i], starts[i] + len(line), line))
    return out


# --- extraction and series ----------------------------------------------------------------

def extract(doc: Doc, company_name: str | None = None) -> list[Found]:
    """Rules 1-8 over one section."""
    lower = doc.text.lower()
    has_backlog, has_orders = "backlog" in lower, "orders" in lower
    if not (has_backlog or has_orders):
        return []
    names = name_words(company_name)
    out: list[Found] = []
    for s0, s1, sent in sentences(doc.text, _KEYWORD):
        low = sent.lower()
        if has_backlog and "backlog" in low:
            out += _backlog(doc, s0, s1, sent, names)
        if has_orders and "orders" in low:
            out += _orders(doc, s0, s1, sent, names)
            out += _growth(doc, s0, s1, sent, names)
    if has_backlog or has_orders:
        out += _tables(doc)
    return [f for f in out if f.written in f.passage]  # rule 8


def _agree(a: Found, b: Found) -> bool:
    return abs(a.value - b.value) <= max(a.precision, b.precision) / 2 * 1.0001


def _choose(clusters: list[list[Found]]) -> list[Found] | None:
    """Rule 9 between disagreeing figures: the strongest source, then the most filings."""
    def rank(c: list[Found]) -> tuple[int, int]:
        return (max(STRENGTH[f.strength] for f in c), len({f.accession_no for f in c}))

    ranked = sorted(clusters, key=rank, reverse=True)
    top, second = rank(ranked[0]), rank(ranked[1])
    if top[0] > second[0] or top[1] > second[1]:
        return ranked[0]
    return None


def series(found: list[Found]) -> list[Series]:
    """Rule 9: one value per metric, date and basis, newest first."""
    out = []
    for metric in METRICS:
        s = Series(metric)
        keys = sorted({(f.period_end, f.basis or "") for f in found if f.metric == metric},
                      reverse=True)
        for when, basis in keys:
            group = [f for f in found if f.metric == metric and f.period_end == when
                     and (f.basis or "") == basis]
            clusters: list[list[Found]] = []
            for f in sorted(group, key=lambda f: f.precision):
                for c in clusters:
                    if _agree(c[0], f):
                        c.append(f)
                        break
                else:
                    clusters.append([f])
            if len(clusters) > 1:
                chosen = _choose(clusters)
                s.conflicts.append({
                    "period_end": when, **({"basis": basis} if basis else {}),
                    "values": sorted({c[0].value for c in clusters}),
                    "kept": chosen[0].value if chosen else None})
                if chosen is None:
                    continue
                clusters = [chosen]
            c = clusters[0]
            seen: set[tuple] = set()
            sources = []
            for f in sorted(c, key=lambda f: (f.filed, f.accession_no, f.start), reverse=True):
                key = (f.accession_no, f.section_key, f.start)
                if key not in seen:
                    seen.add(key)
                    sources.append({"written": f.written, "how": f.how,
                                    "passage": f.passage.strip(), "citation": f.citation()})
            best = c[0]  # the most precise
            s.points.append({
                "period_end": when, "value": best.value, "precision": best.precision,
                "unit": "percent" if metric == "orders_growth" else "USD",
                "span": "instant" if metric == "backlog" else "quarter",
                **({"basis": basis} if basis else {}), "sources": sources})
        out.append(s)
    return out


# --- the route ------------------------------------------------------------------------------

DEFAULT_YEARS = 10
MAX_YEARS = 15
MAX_RELEASES = 4 * MAX_YEARS  # the newest earnings releases read
MAX_SECTIONS = 160
LABELS = {"backlog": "Backlog", "orders": "Orders in the quarter",
          "orders_growth": "Order growth in the quarter, year on year"}


def docs_of(sections: list[dict], release_ends: dict[str, date]) -> list[Doc]:
    """The route's rows as Docs, with rule 7's period: a 10-K/10-Q's period of report (not one
    equal to its filing date, the SEC index quirk), a release's quarter."""
    out = []
    for r in sections:
        if r["form"] == "8-K":
            end = release_ends.get(r["accession_no"])
        else:
            end = r.get("period_of_report")
            end = None if end is None or end == r["filed"] else end
        out.append(Doc(r["accession_no"], r["form"], r["filed"], r["section_key"], end, r["text"],
                       r.get("char_start") or 0))
    return out


def shape(company_id: str, found: list[Found], docs: list[Doc], urls: dict[tuple[str, str], str],
          since: date, now: str) -> dict:
    """The route's answer. Keys `metrics` and `points`, never `rows` or `sections`: verify.py
    reads those as financial rows and filing text."""
    metrics = {}
    for s in series(found):
        for p in s.points:
            for src in p["sources"]:
                c = src["citation"]
                c["document_url"] = urls.get((c["accession_no"], c["section_key"]))
        metrics[s.metric] = {"label": LABELS[s.metric],
                             "unit": "percent" if s.metric == "orders_growth" else "USD",
                             "span": "instant" if s.metric == "backlog" else "quarter",
                             "points": s.points, "conflicts": s.conflicts}
    return {"company_id": company_id, "as_of": now, "since": since, "metrics": metrics,
            "read": {"sections": len(docs), "filings": len({d.accession_no for d in docs}),
                     "from": min((d.filed for d in docs), default=None)},
            "method": METHOD}


def add_text_kpi_routes(app: FastAPI, *, store: Store, current_user: Callable[..., str],
                        require: Callable[[str, str, str], None],
                        today: Callable[[], date]) -> None:
    User = Annotated[str, Depends(current_user)]

    @app.get("/v1/companies/{company_id}/text-kpis", response_model=dict)
    def text_kpis(
        user_id: User, company_id: CompanyId,
        years: Annotated[int, Query(ge=1, le=MAX_YEARS)] = DEFAULT_YEARS,
    ) -> dict:
        """Backlog, orders and order growth as the company's filings state them in words (MD&A,
        the 10-K's Item 1, earnings releases), each value with the passages that state it.
        Rules: query_api/text_kpis.py. Not estimated, not computed, not every company."""
        require(user_id, "filings", company_id)
        now = today()
        try:
            since = now.replace(year=now.year - years)
        except ValueError:  # February 29th
            since = now.replace(year=now.year - years, day=28)
        reports = store.periodic_reports(user_id, company_id)
        rows = store.company_8ks(user_id, company_id, since)
        releases = sorted((r for r in rows if r["form"] == "8-K" and "2.02" in items_of(r)[0]),
                          key=lambda r: (r["filing_date"], r["accession_no"]),
                          reverse=True)[:MAX_RELEASES]
        quarters = periods(reports, releases)
        assign_releases(quarters, releases)
        ends: dict[str, date] = {}
        for q in quarters:
            for r in [q.get("release_row"), *q.get("others", [])]:
                if r is not None:
                    ends[r["accession_no"]] = q["period_end"]
        read = store.kpi_sections(user_id, company_id, since, sorted(ends), MAX_SECTIONS)
        docs = docs_of(read["sections"], ends)
        found = [f for d in docs for f in extract(d, read["name"])]
        urls = {(r["accession_no"], r["section_key"]): r.get("document_url")
                for r in read["sections"]}
        return shape(company_id, found, docs, urls, since, datetime.now(UTC).isoformat())
