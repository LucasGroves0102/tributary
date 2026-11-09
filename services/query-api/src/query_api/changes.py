"""What changed, read for materiality (F3): a second pass, at query time, over one compared
section's stored changes (gold.section_changes: the paragraphs the parser's diff found new,
removed or rewritten, as line offsets; filing_parser/diff.py). It needs only those changes and
the two section texts the /changes route already reads, so no stored row is rebuilt. Pure.

The rules (the spec; the README's "What changed" says the same):

1. Furniture. Page furniture is a blank line, a line without a letter of at most
   PAGE_NUMBER_MAX characters (a page number), or a line of at most FURNITURE_MAX characters
   with fewer than PAGE_ROW_NUMBERS numbers (not a table row) that, with its digits removed and
   case folded, reads "table of contents", "page" or "contents", or occurs FURNITURE_REPEATS or
   more times in its section that way (running headers whose page number changes). Page
   furniture and lines without a letter are dropped from a paragraph; a paragraph left with
   fewer than MIN_WORDS words containing a letter is dropped (counted as `resplit`).
2. Units. On each side, changed paragraphs in text order that nothing but page furniture
   separates are one unit when the first doesn't end a sentence (its last character isn't
   . : ; ! or ?, alone or before a closing quote or bracket) or the second starts with a
   lowercase letter: a paragraph split by a page break or by a line-wrapped document.
3. Matching, each unit used once, in this order:
   a. equal tokens (diff.py's normalisation: numbers masked) → hidden as `resplit` (it only
      differed in how it was split or in furniture);
   b. one unit equal (calendar masked, as in c) to 2 to MAX_RUN consecutive units of the other
      side, joined, nothing but page furniture between them → hidden as `resplit` (split into
      paragraphs, or rejoined);
   c. equal once the calendar is masked too (month names, "may" only before a number,
      "first/second/third/fourth (fiscal) quarter", and "three months" as the quarter and
      "six/nine months" as the year to date, so Q2's "six months ended June 30" equals Q3's
      "nine months ended September 30") → hidden as `calendar`;
   d. equal once figures and one-letter tokens (n/m, n/a) are dropped as well (a table row
      whose cells moved or changed: the words are the same) → hidden as `figures_only`;
   e. fuzzy: Dice >= ALIGN_MIN over calendar-masked tokens sharing a token at most half the
      remaining old units have, best first (ties: closer relative position), never a quarter's
      passage with a year-to-date one (rule 4);
   e'. each such pair takes in its unpaired neighbours on either side (nothing but page
      furniture between), one at a time, while the joined text's Dice with the other side
      grows: a paragraph split and edited (at most MAX_RUN units);
   e''. an unpaired new and removed unit with the same run-in heading (up to eight capitalised
      words and a period: "Gross Profit.", "Total Other Income, Net.") and compatible periods
      pair, in text order: the same passage rewritten past recognition;
   then a pair whose similarity (difflib ratio over calendar-masked tokens) is >= REWRITE_BELOW
   is hidden as `minor`, else rewritten, with a word diff (diff.py's rule 6) over the two
   units' texts;
   f. what's left is new or removed.
4. Period (10-Q passages only: a 10-K's describe its year). A passage that names its period
   has it: "three months ended", "13 weeks ended", "the third quarter" (the quarter); "six" or
   "nine months ended", "first six/nine months", 26 or 39 weeks ended, "first half", "year to
   date" (the year to date); "twelve months ended", 52 weeks ended, "(fiscal) year ended" (the
   year); "three and six months ended" is both. Else the nearest period heading before it in
   its section (a line of at most HEADING_MAX characters starting, after "Comparison of the",
   "(Dollars in millions)", "Results of operations for the" or "For the", with "Three Months
   Ended" and the like, or a line ending ":" whose last three lines name one period), until a
   subsection heading (rule 7) starts something else.
5. Figures: dollar amounts, percentages and basis points, numbers with thousands separators or
   decimals (not years, dates, item or note numbers). `figures.new` are those of the passage
   its comparable passage doesn't state (all of a new one's), `figures.removed` the reverse.
   `moved`: in a rewritten passage, the largest relative change of a figure replaced by one
   figure of the same kind ($, %, plain), |b - a| / max(|a|, |b|).
6. Risk wording: RISK_TERMS (stems) in the passage and not in its comparable one.
7. Weight, by where the passage sits: Risk Factors RISK_FACTORS_WEIGHT; in MD&A the nearest
   subsection heading before it (a line of at most SUBSECTION_MAX characters, no digit, not a
   sentence of more than eight words, starting as one of SUBSECTIONS: liquidity, capital
   resources, debt and cash flows 1.3; results, overview, outlook, segment results 1.0;
   critical accounting 0.8; non-GAAP measures and seasonality 0.6; recent accounting
   pronouncements and forward-looking statements 0.3), else 1.0; a passage with boilerplate
   wording (BOILERPLATE) 0.3.
8. Score = weight x (kind: new 2, removed 1.5, rewritten 1 + 2 x (1 - similarity); + 0.5 per
   new figure up to 2, + 0.25 per removed figure up to 1; + 2 x moved, up to 2; + 1 per new
   risk term up to 2), halved for a passage under SHORT_WORDS words. `why` says which of these
   counted, in words. RANKING is the one-line explanation shown with a ranked list.
9. Filters (the route's): a topic (every term in the passage or its comparable one: a quoted
   phrase as written, else a word prefix with a plural s/es/ies dropped; case ignored), only
   passages with a new figure, a kind. They narrow what is shown, never the hidden counts.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# the stored pass's thresholds (filing_parser.diff), kept equal
MIN_WORDS = 4
ALIGN_MIN = 0.5
REWRITE_BELOW = 0.9
FURNITURE_MAX = 100
FURNITURE_REPEATS = 3
MAX_RUN = 4
SHORT_WORDS = 12
HEADING_MAX = 160
PAGE_NUMBER_MAX = 12  # a line without letters this long at most is a page number ("- 25 -")
PAGE_ROW_NUMBERS = 3  # a line with this many numbers is a table row, never page furniture

RANKING = ("Ranked by what changed: new or removed figures and how far figures moved, new risk "
           "wording, and where the passage sits (Risk Factors and Liquidity above results, "
           "boilerplate last). Not shown: calendar rollovers, paragraphs only split or "
           "rejoined, figure-only table rows and minor edits.")

_SENTENCE_END = re.compile(r"[.:;!?][\"'”’)\]]*$")
_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-"})
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
_TOKEN = re.compile(r"[a-z0-9#]+(?:'[a-z]+)?")
_FURNITURE_WORDS = {"table of contents", "page", "contents", "tableofcontents"}

MONTHS = {"january", "february", "march", "april", "june", "july", "august", "september",
          "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
          "sep", "sept", "oct", "nov", "dec"}  # "may" only before a number
ORDINALS = {"first", "second", "third", "fourth", "1st", "2nd", "3rd", "4th"}
QUARTER_SPAN = {"three"}
YTD_SPAN = {"six", "nine"}

PERIOD = re.compile(
    r"\b(?P<n>three|six|nine|twelve)\s+months\s+ended\b|\bfirst\s+(?P<f>six|nine)\s+months\b"
    r"|\b(?P<w>13|26|39|52)\s*-?\s*weeks\s+ended\b"
    r"|\b(?P<q>first|second|third|fourth)\s+(?:fiscal\s+)?quarter\b"
    r"|\b(?P<h>first\s+half|year[\s-]+to[\s-]+date|(?:fiscal\s+)?year\s+ended)\b", re.I)
_SPAN_WORD = {"three": "three months", "six": "six months", "nine": "nine months",
              "twelve": "year", "13": "three months", "26": "six months", "39": "nine months",
              "52": "year"}
ROLE = {"three months": "quarter", "six months": "ytd", "nine months": "ytd", "year": "year"}
PERIOD_ORDER = ("three months", "six months", "nine months", "year")
_HEADING_LEAD = re.compile(r"^(?:comparison\s+of\s+(?:the\s+)?|\(dollars\s+in\s+\w+\)\s*|"
                           r"results\s+of\s+operations\s+for\s+the\s+|for\s+the\s+)?", re.I)

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
FIGURE = re.compile(
    r"\(?\$\s?" + _NUM + r"\)?(?:\s?(?:million|billion|thousand|trillion)\b)?"
    r"|\b" + _NUM + r"\s?(?:%|percent\b|basis\s+points\b|bps\b)"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d+\b")

RISK_TERMS = ("tariff", "sanction", "export control", "embargo", "litigation", "lawsuit",
              "class action", "investigation", "subpoena", "penalt", "impairment", "write-down",
              "write-off", "restructuring", "going concern", "material weakness", "default",
              "covenant", "waiver", "downgrade", "recall", "cyber", "breach", "ransomware",
              "shortage", "disruption", "work stoppage", "strike", "bankruptcy", "insolvency",
              "delist", "restatement", "non-reliance", "adverse", "uncertain", "headwind",
              "terminat", "loss of", "conflict", "war ", "refund")
_RISK = [(t, re.compile(r"\b" + re.escape(t.strip()) + (r"\b" if t.endswith(" ") else ""), re.I))
         for t in RISK_TERMS]
BOILERPLATE = re.compile(
    r"forward-looking statements|should be read (?:together|in conjunction) with|available free "
    r"of charge|accounting (?:pronouncements|standards update)|safe harbor", re.I)
RISK_FACTORS_WEIGHT = 1.5
RISK_FACTOR_KEYS = {"1A", "II-1A"}
SUBSECTIONS: tuple[tuple[str, re.Pattern, float], ...] = tuple(
    (name, re.compile(pattern, re.I), weight) for name, pattern, weight in (
        ("Liquidity and capital resources",
         r"^(?:liquidity|capital\s+resources|sources\s+and\s+uses|cash\s+flows?\b|debt\b|"
         r"indebtedness|financing\s+arrangements|contractual\s+obligations|off-balance|"
         r"share\s+repurchase|borrowings|credit\s+facilit)", 1.3),
        ("Results of operations",
         r"^(?:results\s+of\s+operations|(?:executive\s+|business\s+)?overview|outlook|"
         r"trends|recent\s+developments|(?:reportable\s+)?(?:operating\s+)?segments?\s+"
         r"(?:results|overview|information)|financial\s+highlights)", 1.0),
        ("Critical accounting estimates", r"^critical\s+accounting", 0.8),
        ("Non-GAAP measures", r"^(?:use\s+of\s+)?non-gaap|^seasonality", 0.6),
        ("Accounting pronouncements and forward-looking statements",
         r"^(?:recent(?:ly)?\s+(?:issued\s+|adopted\s+)?accounting|new\s+accounting|"
         r"forward-looking|cautionary\s+(?:note|statement)|special\s+note)", 0.3),
    ))
SUBSECTION_MAX = 80  # a subsection heading: a line this long at most, no digits, not a sentence

HIDDEN = ("resplit", "calendar", "figures_only", "minor")


# --- text -------------------------------------------------------------------------------------

def normalize(text: str) -> tuple[str, ...]:
    """filing_parser.diff.normalize: NFKC, case and quotes folded, numbers masked as "#"."""
    s = unicodedata.normalize("NFKC", text).lower().translate(_FOLD)
    return tuple(_TOKEN.findall(_NUMBER.sub("#", s)))


def calendar(tokens: Iterable[str]) -> tuple[str, ...]:
    """Tokens with the calendar masked (rule 3c)."""
    t = list(tokens)
    out = []
    for i, w in enumerate(t):
        nxt = t[i + 1] if i + 1 < len(t) else ""
        after = t[i + 2] if i + 2 < len(t) else ""
        if w in MONTHS or (w == "may" and nxt == "#"):
            out.append("<month>")
        elif w in ORDINALS and (nxt == "quarter" or (nxt == "fiscal" and after == "quarter")):
            out.append("<ord>")
        elif nxt == "months" and w in QUARTER_SPAN:
            out.append("<quarter>")
        elif nxt == "months" and w in YTD_SPAN:
            out.append("<ytd>")
        else:
            out.append(w)
    return tuple(out)


def bare(tokens: Iterable[str]) -> tuple[str, ...]:
    """Calendar-masked tokens without figures or single letters (n/m, n/a): rule 3d."""
    return tuple(w for w in tokens if w != "#" and len(w) > 1)


def ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.rstrip()))


class Furniture:
    """Rule 1 for one section's text."""

    def __init__(self, text: str) -> None:
        self._repeats = Counter(self._key(ln) for ln in text.split("\n")
                                if 0 < len(ln.strip()) <= FURNITURE_MAX and not _row(ln))

    @staticmethod
    def _key(line: str) -> str:
        return re.sub(r"[\d\W_]+", " ", line.lower()).strip()

    def page(self, line: str) -> bool:
        """Page furniture: a blank line, a page number, "Table of Contents", a running header
        (repeated with its page number masked). Never a table row (PAGE_ROW_NUMBERS figures)."""
        s = line.strip()
        if not s:
            return True
        if not any(c.isalpha() for c in s):
            return len(s) <= PAGE_NUMBER_MAX
        if len(s) > FURNITURE_MAX or _row(s):
            return False
        key = self._key(s)
        return key in _FURNITURE_WORDS or self._repeats[key] >= FURNITURE_REPEATS

    def __call__(self, line: str) -> bool:
        """Rule 1: page furniture, or a line without a letter (a row of figures)."""
        s = line.strip()
        return not any(c.isalpha() for c in s) or self.page(s)

    def only(self, text: str) -> bool:
        """Whether nothing but page furniture separates two paragraphs (rule 2): a table row
        between them keeps them apart."""
        return all(self.page(ln) for ln in text.split("\n"))


def _row(line: str) -> bool:
    return len(re.findall(r"\d+(?:[.,]\d+)*", line)) >= PAGE_ROW_NUMBERS


# --- units ------------------------------------------------------------------------------------

@dataclass
class Unit:
    side: str  # "new" | "old"
    spans: list[tuple[int, int]]
    ordinal: int
    parts: int = 1
    text: str = ""
    tokens: tuple[str, ...] = ()
    cal: tuple[str, ...] = ()
    bare: tuple[str, ...] = ()
    period: str | None = None
    used: bool = False

    @property
    def start(self) -> int:
        return self.spans[0][0]

    @property
    def end(self) -> int:
        return self.spans[-1][1]

    def flat(self) -> list[int]:
        return [x for s in self.spans for x in s]


def _pairs(flat: list[int] | None) -> list[tuple[int, int]]:
    flat = flat or []
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]


def _finish(u: Unit, text: str) -> Unit:
    u.text = " ".join(text[a:b].strip() for a, b in u.spans)
    u.tokens = normalize(u.text)
    u.cal = calendar(u.tokens)
    u.bare = bare(u.cal)
    return u


def units(side: str, text: str, paragraphs: list[tuple[int, list[int]]],
          furniture: Furniture) -> tuple[list[Unit], int]:
    """A side's changed paragraphs ((ordinal, flat spans)) as units (rules 1, 2), in text order,
    and how many paragraphs were furniture only."""
    kept: list[Unit] = []
    dropped = 0
    for ordinal, flat in sorted(paragraphs, key=lambda p: (p[1][0] if p[1] else 0, p[0])):
        spans = [(a, b) for a, b in _pairs(flat) if not furniture(text[a:b])]
        words = [w for a, b in spans for w in text[a:b].split() if any(c.isalpha() for c in w)]
        if len(words) < MIN_WORDS:
            dropped += 1
            continue
        kept.append(Unit(side, spans, ordinal))
    merged: list[Unit] = []
    for u in kept:
        prev = merged[-1] if merged else None
        if prev is not None and furniture.only(text[prev.end:u.start]):
            last = text[prev.spans[-1][0]:prev.spans[-1][1]].strip()
            first = text[u.spans[0][0]:u.spans[0][1]].lstrip()
            if not ends_sentence(last) or first[:1].islower():
                prev.spans += u.spans
                prev.ordinal = min(prev.ordinal, u.ordinal)
                prev.parts += u.parts
                continue
        merged.append(u)
    return [_finish(u, text) for u in merged], dropped


# --- period, subsection, figures --------------------------------------------------------------

def periods_in(text: str) -> list[str]:
    """The periods a passage names (rule 4), in PERIOD_ORDER."""
    found = set()
    for m in PERIOD.finditer(text):
        if m.group("n") or m.group("f"):
            found.add(_SPAN_WORD[(m.group("n") or m.group("f")).lower()])
        elif m.group("w"):
            found.add(_SPAN_WORD[m.group("w")])
        elif m.group("q"):
            found.add("three months")
        else:
            h = " ".join(m.group("h").lower().replace("-", " ").split())
            found.add("six months" if h == "first half" else
                      "year" if h.endswith("year ended") else "year to date")
    if "year to date" in found:
        found.discard("year to date")
        if not found & {"six months", "nine months"}:
            found.add("six months")
    # "three and six months ended": the regex sees "six months ended" only
    if re.search(r"\bthree\s+and\s+(six|nine)\s+months\s+ended\b", text, re.I):
        found.add("three months")
    return [p for p in PERIOD_ORDER if p in found]


def period_label(found: list[str]) -> str | None:
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    spans = [p.split()[0] for p in found if p != "year"]
    return f"{' and '.join(spans)} months" if len(spans) == len(found) else ", ".join(found)


def roles(label: str | None) -> set[str]:
    """The quarter, the year to date or the year, for rule 3e."""
    words = set((label or "").replace(",", " ").split())
    return {r for p, r in ROLE.items() if p.split()[0] in words}


class Context:
    """Where a passage sits in one section's text: its period heading (rule 4) and its
    subsection (rule 7)."""

    def __init__(self, text: str) -> None:
        self.marks: list[tuple[int, str, str | None, float | None]] = []  # pos, kind, value
        lines, pos = [], 0
        for ln in text.split("\n"):
            lines.append((pos, ln.strip()))
            pos += len(ln) + 1
        for i, (at, s) in enumerate(lines):
            if not s or len(s) > HEADING_MAX:
                continue
            sub = self._subsection(s)
            if sub:
                self.marks.append((at, "sub", sub[0], sub[1]))
                continue
            lead = _HEADING_LEAD.sub("", s)
            found = periods_in(lead[:60]) if PERIOD.match(lead) else []
            if not found and s.endswith(":"):
                window = " ".join(x for _, x in lines[max(0, i - 2):i + 1])
                found = periods_in(window)
                found = found if len(found) == 1 else []
            if found:
                self.marks.append((at, "period", period_label(found), None))

    @staticmethod
    def _subsection(s: str) -> tuple[str, float] | None:
        """A subsection heading (rule 7): at most SUBSECTION_MAX characters, no digit (a table
        row), not a sentence of more than eight words."""
        if len(s) > SUBSECTION_MAX or any(c.isdigit() for c in s) or (
                s.endswith(".") and len(s.split()) > 8):
            return None
        for name, pattern, weight in SUBSECTIONS:
            if pattern.match(s):
                return name, weight
        return None

    def at(self, pos: int) -> tuple[str | None, tuple[str, float] | None]:
        """(period heading, subsection) in force at `pos`."""
        period, sub = None, None
        for at, kind, value, weight in self.marks:
            if at > pos:
                break
            if kind == "sub":
                sub, period = (value, weight), None
            else:
                period = value
        return period, sub  # type: ignore[return-value]


def figures(text: str) -> list[str]:
    return [" ".join(m.group(0).split()) for m in FIGURE.finditer(text)]


def _key(fig: str) -> str:
    return re.sub(r"[\s$()]", "", fig.lower())


def _value(fig: str) -> tuple[str, float] | None:
    kind = "%" if "%" in fig or "percent" in fig or "basis" in fig or "bps" in fig else (
        "$" if "$" in fig else "n")
    m = re.search(r"\d[\d,]*(?:\.\d+)?", fig)
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    scale = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
    for word, k in scale.items():
        if word in fig.lower():
            v *= k
    return kind, (-v if fig.strip().startswith("(") else v)


def new_figures(text: str, other: str) -> tuple[list[str], list[str]]:
    """Figures in `text` and not in `other` (as a multiset), and the reverse."""
    a, b = figures(text), figures(other)
    left = Counter(_key(f) for f in b)
    added = []
    for f in a:
        if left[_key(f)] > 0:
            left[_key(f)] -= 1
        else:
            added.append(f)
    right = Counter(_key(f) for f in a)
    removed = []
    for f in b:
        if right[_key(f)] > 0:
            right[_key(f)] -= 1
        else:
            removed.append(f)
    return added, removed


def moved(runs: list[list[str]]) -> dict | None:
    """Rule 5's largest relative change of a figure replaced in a word diff."""
    best = None
    for i in range(len(runs) - 1):
        (op1, t1), (op2, t2) = runs[i], runs[i + 1]
        if (op1, op2) != ("-", "+"):
            continue
        olds, news = figures(t1), figures(t2)
        if len(olds) != len(news):
            continue
        for o, n in zip(olds, news, strict=True):
            vo, vn = _value(o), _value(n)
            if not vo or not vn or vo[0] != vn[0]:
                continue
            top = max(abs(vo[1]), abs(vn[1]))
            if top == 0:
                continue
            change = abs(vn[1] - vo[1]) / top
            if best is None or change > best["change"]:
                best = {"from": o, "to": n, "change": round(change, 3)}
    return best


def risk_terms(text: str, other: str | None) -> list[str]:
    return [t.strip() for t, rx in _RISK if rx.search(text) and not (other and rx.search(other))]


# --- word diff (filing_parser.diff rule 6) ----------------------------------------------------

_TAGS = {"equal": "e", "replace": "r", "delete": "d", "insert": "i"}


def _word_key(w: str) -> str:
    return unicodedata.normalize("NFKC", w).lower().translate(_FOLD)


def word_opcodes(old: str, new: str) -> list[list]:
    sm = SequenceMatcher(None, [_word_key(w) for w in old.split()],
                         [_word_key(w) for w in new.split()], autojunk=False)
    return [[_TAGS[t], i1, i2, j1, j2] for t, i1, i2, j1, j2 in sm.get_opcodes()]


# --- the pass ---------------------------------------------------------------------------------

@dataclass
class Change:
    kind: str
    new: Unit | None
    old: Unit | None
    similarity: float | None = None
    words: list[list] | None = None  # opcodes over (old text, new text)
    ordinal: int = 0
    period: str | None = None
    subsection: str | None = None
    weight: float = 1.0
    figures_new: list[str] = field(default_factory=list)
    figures_removed: list[str] = field(default_factory=list)
    moved: dict | None = None
    risk: list[str] = field(default_factory=list)
    score: float = 0.0
    why: list[str] = field(default_factory=list)


@dataclass
class Reading:
    changes: list[Change]
    hidden: dict[str, int]


def _dice(a: Counter, b: Counter, na: int, nb: int) -> float:
    return 2 * sum((a & b).values()) / (na + nb) if na + nb else 0.0


def _compatible(a: Unit, b: Unit) -> bool:
    ra, rb = roles(a.period), roles(b.period)
    return not ra or not rb or bool(ra & rb)


def _runs(us: list[Unit], text: str, furniture: Furniture) -> list[tuple[list[Unit], tuple]]:
    """Runs of 2..MAX_RUN consecutive unused units with only furniture between them, with their
    joined tokens."""
    out = []
    for i in range(len(us)):
        if us[i].used:
            continue
        run, tokens = [us[i]], us[i].cal
        for j in range(i + 1, min(i + MAX_RUN, len(us))):
            if us[j].used or not furniture.only(text[run[-1].end:us[j].start]):
                break
            run = [*run, us[j]]
            tokens = tokens + us[j].cal
            out.append((run, tokens))
    return out


def read_section(section_key: str, form: str, text: str, prior_text: str,
                 stored: list[dict]) -> Reading:
    """Rules 1-8 for one compared section of a filing of this form: `stored` are its
    gold.section_changes rows (kind, ordinal, new_spans, prior_spans). Periods (rule 4) are a
    10-Q's: a 10-K's passages all describe its year."""
    fn, fo = Furniture(text), Furniture(prior_text)
    hidden: dict[str, int] = dict.fromkeys(HIDDEN, 0)
    new_units, dn = units("new", text, [(c["ordinal"], c["new_spans"]) for c in stored
                                        if c.get("new_spans")], fn)
    old_units, do = units("old", prior_text, [(c["ordinal"], c["prior_spans"]) for c in stored
                                              if c.get("prior_spans")], fo)
    hidden["resplit"] += dn + do
    cn, co = Context(text), Context(prior_text)
    if form.startswith("10-Q"):
        for us, ctx in ((new_units, cn), (old_units, co)):
            for u in us:
                u.period = period_label(periods_in(u.text)) or ctx.at(u.start)[0]

    # a. equal, c. calendar, d. figures only: one unit for one unit, in order
    for attr, reason in (("tokens", "resplit"), ("cal", "calendar"), ("bare", "figures_only")):
        waiting: dict[tuple, list[Unit]] = {}
        for o in old_units:
            if not o.used:
                waiting.setdefault(getattr(o, attr), []).append(o)
        for n in new_units:
            if n.used:
                continue
            match = next((o for o in waiting.get(getattr(n, attr), []) if not o.used), None)
            if match is not None:
                n.used = match.used = True
                hidden[reason] += 1
        if attr == "tokens":  # b. one against a run of the other side, joined
            for us, them, them_text, f in ((new_units, old_units, prior_text, fo),
                                           (old_units, new_units, text, fn)):
                runs = _runs(them, them_text, f)
                for u in us:
                    if u.used:
                        continue
                    for run, tokens in runs:
                        if tokens == u.cal and not any(r.used for r in run):
                            u.used = True
                            for r in run:
                                r.used = True
                            hidden["resplit"] += 1
                            break

    # e. fuzzy, over calendar-masked tokens
    pairs = _fuzzy([n for n in new_units if not n.used], [o for o in old_units if not o.used])
    # e'. a pair takes in its unpaired neighbours when that makes it closer (split and edited)
    groups = [(_extend([n], new_units, o, text, fn), o) for n, o in pairs]
    groups = [(ns, _extend([o], old_units, _joined("new", ns, text), prior_text, fo))
              for ns, o in groups]
    # e''. same inline heading ("Gross Profit.") and period, rewritten past recognition
    groups += [([n], [o]) for n, o in _same_heading(new_units, old_units)]
    out: list[Change] = []
    for ns, os in groups:
        n, o = _joined("new", ns, text), _joined("old", os, prior_text)
        sim = SequenceMatcher(None, o.cal, n.cal, autojunk=False).ratio()
        if sim >= REWRITE_BELOW:
            hidden["minor"] += 1
            continue
        out.append(Change("rewritten", n, o, round(sim, 3), word_opcodes(o.text, n.text),
                          ordinal=n.ordinal))
    out += [Change("new", n, None, ordinal=n.ordinal) for n in new_units if not n.used]
    out += [Change("removed", None, o, ordinal=o.ordinal) for o in old_units if not o.used]
    for c in out:
        _features(c, section_key, cn if c.new else co)
    out.sort(key=lambda c: (c.ordinal, c.kind != "removed"))
    return Reading(out, hidden)


def _fuzzy(rest_new: list[Unit], rest_old: list[Unit]) -> list[tuple[Unit, Unit]]:
    """Rule 3e's pairs, marked used: Dice >= ALIGN_MIN over calendar-masked tokens sharing a
    token that at most half the old units have, best first (ties: closer relative position)."""
    bags = {id(o): Counter(o.cal) for o in rest_old}
    df = Counter(t for o in rest_old for t in bags[id(o)])
    common = max(1, len(rest_old) // 2)
    postings: dict[str, list[Unit]] = {}
    for o in rest_old:
        for t in bags[id(o)]:
            if df[t] <= common:
                postings.setdefault(t, []).append(o)
    place = {id(o): i for i, o in enumerate(rest_old)}
    scored = []
    for j, n in enumerate(rest_new):
        bag = Counter(n.cal)
        seen: dict[int, Unit] = {}
        for t in bag:
            for o in postings.get(t, ()):
                seen[id(o)] = o
        for o in seen.values():
            if not _compatible(n, o):
                continue
            d = _dice(bags[id(o)], bag, len(o.cal), len(n.cal))
            if d >= ALIGN_MIN:
                i = place[id(o)]
                gap = abs(i / max(1, len(rest_old)) - j / max(1, len(rest_new)))
                scored.append((-d, gap, j, i, n, o))
    scored.sort(key=lambda x: x[:4])
    pairs = []
    for _, _, _, _, n, o in scored:
        if n.used or o.used:
            continue
        n.used = o.used = True
        pairs.append((n, o))
    return pairs


def _joined(side: str, group: list[Unit], text: str) -> Unit:
    """One unit of a group of units on one side, in text order."""
    if len(group) == 1:
        return group[0]
    group = sorted(group, key=lambda u: u.start)
    u = Unit(side, [s for g in group for s in g.spans], min(g.ordinal for g in group),
             sum(g.parts for g in group), period=group[0].period, used=True)
    return _finish(u, text)


def _extend(group: list[Unit], side: list[Unit], other: Unit, text: str,
            furniture: Furniture) -> list[Unit]:
    """Take in unused neighbours of the group on its side (nothing but furniture between),
    one at a time, while the joined text's Dice with `other` grows."""
    order = {id(u): i for i, u in enumerate(side)}
    bag = Counter(other.cal)

    def dice(g: list[Unit]) -> float:
        tokens = [t for u in sorted(g, key=lambda u: u.start) for t in u.cal]
        return _dice(Counter(tokens), bag, len(tokens), len(other.cal))

    best = dice(group)
    while len(group) < MAX_RUN:
        lo = min(order[id(u)] for u in group)
        hi = max(order[id(u)] for u in group)
        options = []
        if lo > 0 and not side[lo - 1].used \
                and furniture.only(text[side[lo - 1].end:side[lo].start]):
            options.append(side[lo - 1])
        if hi + 1 < len(side) and not side[hi + 1].used \
                and furniture.only(text[side[hi].end:side[hi + 1].start]):
            options.append(side[hi + 1])
        scored = sorted(((dice([*group, u]), order[id(u)], u) for u in options),
                        key=lambda x: (-x[0], x[1]))
        if not scored or scored[0][0] <= best:
            break
        best = scored[0][0]
        scored[0][2].used = True
        group = [*group, scored[0][2]]
    return group


_HEADING = re.compile(r"^((?:[A-Z][\w&’'-]*|and|of|or|the|in|to|for|on|from|by|a|an|net|"
                      r"\([\w\s]+\))(?:,?\s+(?:[A-Z][\w&’'-]*|and|of|or|the|in|to|for|on|"
                      r"from|by|a|an|net|\([\w\s]+\))){0,7})\.\s")


def inline_heading(text: str) -> str | None:
    """A paragraph's run-in heading ("Gross Profit.", "Total Other Income, Net."): up to eight
    capitalised words before the first period, then a sentence."""
    m = _HEADING.match(text)
    return " ".join(m.group(1).lower().split()) if m and len(m.group(1)) <= 60 else None


def _same_heading(new_units: list[Unit], old_units: list[Unit]) -> list[tuple[Unit, Unit]]:
    """Rule 3e'': an unpaired new and removed unit with the same run-in heading, each the only
    one with it on its side among the unpaired, and compatible periods."""
    def index(us: list[Unit]) -> dict[str, list[Unit]]:
        out: dict[str, list[Unit]] = {}
        for u in us:
            h = inline_heading(u.text) if not u.used else None
            if h:
                out.setdefault(h, []).append(u)
        return out
    olds = index(old_units)
    pairs = []
    for h, ns in index(new_units).items():
        os = olds.get(h, [])
        free = [(n, o) for n in ns for o in os if _compatible(n, o)]
        # one-to-one within the heading: pair them in text order, quarter with quarter
        for n, o in free:
            if not n.used and not o.used:
                n.used = o.used = True
                pairs.append((n, o))
    return pairs


def render_words(old: str, new: str, opcodes: list[list]) -> list[list[str]]:
    """research.render_words (diff.py's), here so the pass needs nothing else."""
    a, b = old.split(), new.split()
    runs: list[list[str]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        for op, words in (("=", b[j1:j2]),) if tag == "e" else (("-", a[i1:i2]),
                                                                  ("+", b[j1:j2])):
            if not words:
                continue
            if runs and runs[-1][0] == op:
                runs[-1][1] += " " + " ".join(words)
            else:
                runs.append([op, " ".join(words)])
    return runs


def _features(c: Change, section_key: str, ctx: Context) -> None:
    """Rules 4-8 for one change."""
    u = c.new or c.old
    assert u is not None
    c.period = u.period
    text, other = (c.new.text if c.new else ""), (c.old.text if c.old else "")
    c.figures_new, c.figures_removed = new_figures(text, other)
    if c.kind == "rewritten" and c.words is not None:
        c.moved = moved(render_words(other, text, c.words))
    c.risk = risk_terms(text, other if c.new else None) if c.new else []
    _, sub = ctx.at(u.start)
    if section_key in RISK_FACTOR_KEYS:
        c.weight, c.subsection = RISK_FACTORS_WEIGHT, "Risk factors"
    elif sub:
        c.subsection, c.weight = sub
    if BOILERPLATE.search(u.text):
        c.weight, c.subsection = 0.3, c.subsection or "Boilerplate"
    base = {"new": 2.0, "removed": 1.5}.get(c.kind, 1.0 + 2 * (1 - (c.similarity or 0)))
    figs = min(2.0, 0.5 * len(c.figures_new)) + min(1.0, 0.25 * len(c.figures_removed))
    move = min(2.0, 2 * c.moved["change"]) if c.moved else 0.0
    risk = min(2.0, 1.0 * len(c.risk))
    words = sum(1 for w in u.text.split() if any(ch.isalpha() for ch in w))
    short = 0.5 if words < SHORT_WORDS else 1.0
    c.score = round(c.weight * (base + figs + move + risk) * short, 2)
    why = [{"new": "new passage", "removed": "removed passage"}.get(
        c.kind, f"rewritten ({round(100 * (1 - (c.similarity or 0)))}% different)")]
    if c.figures_new:
        n = len(c.figures_new)
        why.append(f"{n} new figure{'s' if n != 1 else ''}")
    if c.figures_removed:
        n = len(c.figures_removed)
        why.append(f"{n} figure{'s' if n != 1 else ''} gone")
    if c.moved and c.moved["change"] >= 0.1:
        why.append(f"{c.moved['from']} → {c.moved['to']}")
    if c.risk:
        why.append("new risk wording: " + ", ".join(c.risk[:3]))
    if c.subsection:
        why.append(c.subsection)
    if short < 1:
        why.append("short")
    c.why = why


# --- filters ----------------------------------------------------------------------------------

def topic_terms(q: str | None) -> list[str]:
    """Rule 9's terms: quoted phrases as written, words as prefixes with a plural dropped."""
    if not q:
        return []
    out = [" ".join(p.split()).lower() for p in re.findall(r'"([^"]+)"', q) if p.strip()]
    for w in re.findall(r"[\w$%.,'-]+", re.sub(r'"[^"]*"', " ", q).lower()):
        w = w.strip(".,'-")
        if len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return list(dict.fromkeys(out))


def matches_topic(terms: list[str], *texts: str | None) -> bool:
    body = " ".join(" ".join(t.split()) for t in texts if t).lower()
    for t in terms:
        if " " in t:
            if t not in body:
                return False
        elif not re.search(r"(?<![\w])" + re.escape(t), body):
            return False
    return True
