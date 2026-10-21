"""Paragraph-level diff of one section between two filings. Pure, no I/O.

What changed in Risk Factors or MD&A matters for its substance (a new risk, a dropped one, a
rewritten paragraph), not for how many words moved, so the output is a list of paragraphs
classified new / removed / rewritten, with a word-level diff for rewritten ones. Unchanged
paragraphs are only counted.

1. Lines. Section text is the parser's html_to_text: one line per block element. Page
   furniture is skipped: lines without a letter (page numbers, table cells of figures) and
   lines of at most 100 characters that are "Table of Contents" or occur 3 or more times in
   the section (running headers).
2. Paragraphs. A line that doesn't end a sentence (last character not one of . : ; ! ? or a
   closing quote or bracket) followed by a line starting with a lowercase letter is joined to
   it (a paragraph split by a page break, possibly around furniture). A paragraph's text is
   its lines joined by a space; its spans are the lines' [start, end) offsets in the section
   text. A paragraph is kept when it has at least MIN_WORDS words containing a letter and
   letters are at least half of its non-space characters (drops table rows and labels).
3. Normalization: NFKC, lowercase, curly quotes and dashes folded to ASCII, then (when
   mask_numbers, the default) every number with its $ , . % becomes "#", so "fiscal 2025" and
   "fiscal 2026" compare equal; then only word tokens ([a-z0-9#]+, with an apostrophe suffix)
   are kept.
4. Alignment. (a) Exact: paragraphs with equal normalized text are paired in order, wherever
   they are (a moved paragraph is unchanged). (b) Fuzzy: every remaining pair sharing a token
   that occurs in at most half of the remaining old paragraphs is scored with the Dice
   coefficient of their token multisets; pairs with Dice >= ALIGN_MIN are taken greedily,
   best first (ties: closer relative position, then document order). Each paragraph is paired
   at most once.
5. Classification. Exact pairs are unchanged (numbers_only counts those whose unmasked text
   differs). A fuzzy pair's similarity is difflib's ratio over the two token sequences: below
   REWRITE_BELOW it is rewritten, otherwise a minor edit (counted as unchanged). Unpaired new
   paragraphs are new, unpaired old ones removed.
6. Word diff (rewritten only): difflib (autojunk off) over the whitespace tokens of the two
   paragraph texts, compared case- and quote-insensitively without masking (changed figures
   show). Stored as opcodes [tag, i1, i2, j1, j2] (tag e/r/d/i: equal, replace, delete,
   insert; i old token indexes, j new) so gold holds no text; render_words turns them into
   [op, text] runs: "=" both, "-" prior only, "+" this filing only.
7. Order: new and rewritten paragraphs in this filing's order; a removed paragraph right after
   the new-side position of the nearest preceding old paragraph that was paired.

Bump ALGORITHM whenever any rule or threshold changes: the index job recomputes every diff
made by another version. query_api.changes renders the opcodes the same way as render_words.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

ALGORITHM = "diff-1"
MIN_WORDS = 4
ALIGN_MIN = 0.5
REWRITE_BELOW = 0.9
FURNITURE_MAX = 100
FURNITURE_REPEATS = 3

_SENTENCE_END = tuple(".:;!?\"')]”’")
_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-"})
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
_TOKEN = re.compile(r"[a-z0-9#]+(?:'[a-z]+)?")
_TAGS = {"equal": "e", "replace": "r", "delete": "d", "insert": "i"}


@dataclass(frozen=True)
class Paragraph:
    spans: tuple[tuple[int, int], ...]  # [start, end) of each line in the section text
    text: str
    tokens: tuple[str, ...]  # normalized, numbers masked
    plain: tuple[str, ...]  # normalized, numbers kept

    @property
    def start(self) -> int:
        return self.spans[0][0]

    @property
    def end(self) -> int:
        return self.spans[-1][1]


@dataclass(frozen=True)
class Change:
    kind: str  # new | removed | rewritten
    new: Paragraph | None
    old: Paragraph | None
    similarity: float | None = None
    words: list[list] | None = None  # opcodes [tag, i1, i2, j1, j2]


@dataclass
class Diff:
    paragraphs: int = 0
    prior_paragraphs: int = 0
    unchanged: int = 0  # exact (after normalization) plus minor edits
    minor_edits: int = 0
    numbers_only: int = 0
    rewritten: int = 0
    new: int = 0
    removed: int = 0
    changes: list[Change] = field(default_factory=list)


def normalize(text: str, mask_numbers: bool = True) -> tuple[str, ...]:
    s = unicodedata.normalize("NFKC", text).lower().translate(_FOLD)
    if mask_numbers:
        s = _NUMBER.sub("#", s)
    return tuple(_TOKEN.findall(s))


def _lines(text: str) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    for line in text.split("\n"):
        out.append((pos, pos + len(line), line.strip()))
        pos += len(line) + 1
    return out


def paragraphs(text: str) -> list[Paragraph]:
    lines = [ln for ln in _lines(text) if ln[2]]
    repeats = Counter(s for _, _, s in lines if len(s) <= FURNITURE_MAX)

    def furniture(s: str) -> bool:
        return not any(c.isalpha() for c in s) or (len(s) <= FURNITURE_MAX and (
            repeats[s] >= FURNITURE_REPEATS or s.lower() == "table of contents"))

    groups: list[list[tuple[int, int, str]]] = []
    for ln in lines:
        if furniture(ln[2]):
            continue
        if groups and ln[2][:1].islower() and not groups[-1][-1][2].endswith(_SENTENCE_END):
            groups[-1].append(ln)
        else:
            groups.append([ln])
    out = []
    for g in groups:
        body = " ".join(s for _, _, s in g)
        words = [w for w in body.split() if any(c.isalpha() for c in w)]
        dense = "".join(body.split())
        if len(words) < MIN_WORDS or sum(c.isalpha() for c in dense) * 2 < len(dense):
            continue
        out.append(Paragraph(tuple((a, b) for a, b, _ in g), body, normalize(body),
                             normalize(body, False)))
    return out


def _word_key(w: str) -> str:
    return unicodedata.normalize("NFKC", w).lower().translate(_FOLD)


def word_opcodes(old: str, new: str) -> list[list]:
    sm = SequenceMatcher(None, [_word_key(w) for w in old.split()],
                         [_word_key(w) for w in new.split()], autojunk=False)
    return [[_TAGS[t], i1, i2, j1, j2] for t, i1, i2, j1, j2 in sm.get_opcodes()]


def render_words(old: str, new: str, opcodes: list[list]) -> list[list[str]]:
    """Opcodes as [op, text] runs: "=" in both, "-" prior only, "+" this filing only."""
    a, b = old.split(), new.split()
    runs: list[list[str]] = []

    def emit(op: str, words: list[str]) -> None:
        if not words:
            return
        if runs and runs[-1][0] == op:
            runs[-1][1] += " " + " ".join(words)
        else:
            runs.append([op, " ".join(words)])

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "e":
            emit("=", b[j1:j2])
        else:
            emit("-", a[i1:i2])
            emit("+", b[j1:j2])
    return runs


def _dice(a: Counter, b: Counter, na: int, nb: int) -> float:
    return 2 * sum((a & b).values()) / (na + nb) if na + nb else 0.0


def diff(old_text: str, new_text: str) -> Diff:
    old, new = paragraphs(old_text), paragraphs(new_text)
    result = Diff(paragraphs=len(new), prior_paragraphs=len(old))
    pair: dict[int, int] = {}  # new index -> old index
    fuzzy: dict[int, float] = {}  # new index -> similarity, fuzzy pairs only

    # (a) exact
    waiting: dict[tuple[str, ...], list[int]] = {}
    for i, p in enumerate(old):
        waiting.setdefault(p.tokens, []).append(i)
    for j, p in enumerate(new):
        if waiting.get(p.tokens):
            i = waiting[p.tokens].pop(0)
            pair[j] = i
            result.unchanged += 1
            if old[i].plain != p.plain:
                result.numbers_only += 1

    # (b) fuzzy
    paired_old = set(pair.values())
    rest_old = [i for i in range(len(old)) if i not in paired_old]
    rest_new = [j for j in range(len(new)) if j not in pair]
    old_bags = {i: Counter(old[i].tokens) for i in rest_old}
    df = Counter(t for i in rest_old for t in old_bags[i])
    common = max(1, len(rest_old) // 2)
    postings: dict[str, list[int]] = {}
    for i in rest_old:
        for t in old_bags[i]:
            if df[t] <= common:
                postings.setdefault(t, []).append(i)
    scored = []
    for j in rest_new:
        bag = Counter(new[j].tokens)
        for i in {i for t in bag if t in postings for i in postings[t]}:
            d = _dice(old_bags[i], bag, len(old[i].tokens), len(new[j].tokens))
            if d >= ALIGN_MIN:
                gap = abs(i / len(old) - j / len(new))
                scored.append((-d, gap, j, i))
    scored.sort()
    taken: set[int] = set()
    for _, _, j, i in scored:
        if j in pair or i in taken:
            continue
        pair[j] = i
        taken.add(i)
        fuzzy[j] = SequenceMatcher(None, old[i].tokens, new[j].tokens, autojunk=False).ratio()

    # classify and order
    keyed: list[tuple[tuple[float, int], Change]] = []
    for j, p in enumerate(new):
        if j not in pair:
            result.new += 1
            keyed.append(((j, 0), Change("new", p, None)))
        elif j in fuzzy:
            sim, o = fuzzy[j], old[pair[j]]
            if sim < REWRITE_BELOW:
                result.rewritten += 1
                keyed.append(((j, 0), Change("rewritten", p, o, round(sim, 3),
                                             word_opcodes(o.text, p.text))))
            else:
                result.unchanged += 1
                result.minor_edits += 1
    new_of_old = {i: j for j, i in pair.items()}
    anchor = -0.5
    for i, o in enumerate(old):
        if i in new_of_old:
            anchor = new_of_old[i] + 0.5
        else:
            result.removed += 1
            keyed.append(((anchor, i), Change("removed", None, o)))
    keyed.sort(key=lambda kc: kc[0])
    result.changes = [c for _, c in keyed]
    return result
