"""Balance-sheet events an 8-K announces without the item that reports them (F1b): a completed
acquisition or disposition, or a new financing, announced under Item 7.01 (Regulation FD) or
8.01 (Other Events) instead of 2.01 (completion of an acquisition or disposition) or 2.03 (a new
direct financial obligation). events.py flags the same figures for them as for 2.01 and 2.03.
Pure functions; the rules (the spec, mirrored in the README):

1. Which 8-Ks: an 8-K with item 7.01 or 8.01 and without 2.02. An earnings release recaps its
   quarter, so a deal or a loan it mentions may be inside the period it reports; it is never
   read. A kind whose item the 8-K also has (2.01 for a deal, 2.03 for a financing) is already
   flagged by that item and isn't read either. Which 8-K is after the period end, and which
   figures each kind can change, is events.py's (AFFECTS of 2.01 and 2.03).
2. What is read: the opening of the 8-K's press release (its lowest-numbered EX-99 exhibit),
   then of its 7.01 and 8.01 sections: the first events.HEAD_CHARS characters of each, which
   hold the headline and the lead paragraph. Later paragraphs repeat them, quote management or
   are boilerplate (about the company, forward-looking statements).
3. Sentences: a line is joined to the next unless it ends a sentence (. ! ? : ; before an
   optional closing quote or bracket) or the next line starts with an uppercase letter (a
   headline stands alone); then the text is cut after . ! ? followed by a space and an uppercase
   letter or an opening quote. Each sentence is read alone.
4. A completed deal: a sentence with "completes", "completed", "closes", "closed", "completion
   of" or "closing of" followed within six words by acquisition, merger, business combination,
   sale (not a sale of a dollar amount or a number of shares), divestiture, disposition,
   spin-off or separation; or "has/have acquired", "has/have sold", "has/have divested", or a
   headline's "Acquires". It doesn't count when the sentence says the deal is still ahead
   (DEAL_PENDING anywhere in it: an agreement to acquire, a definitive agreement or plan of
   merger, expected to close or be completed, subject to closing, upon or pending completion)
   or a NOT_DONE word comes before the phrase (agrees, expects, anticipates, intends, plans,
   will, would, upon, following, after, prior to, pending, subject to, proposed, once, until,
   if). An agreement or an intent to acquire is not a completion.
5. A new financing: a sentence with priced / prices / pricing of ... notes, debentures or bonds
   (within 120 characters); completed / closed / settled an offering, sale, issuance or private
   placement of ... notes; has issued ... notes, or issued $X ... notes; entered into a credit
   agreement, credit facility, term loan, revolving facility, loan agreement or delayed-draw
   facility (not an amendment); borrowed or drew under a term loan or credit facility;
   announces / closes / secures / obtains / completes a new credit facility or term loan; or
   priced / pricing of the offering when the section has already named notes, debentures or
   bonds ("TKO announced the pricing of the Offering described above"). It doesn't count when
   a NOT_YET word comes before the end of the phrase (proposed, intends, plans, launches,
   commences, may (not "May 1"), seeks, would, will, expects, if, amendment). A guidance update,
   a share repurchase, a dividend or a redemption names none of these, so none is a financing.
6. The first sentence that reads as either kind decides that kind (a financing is tried first,
   so "closed the sale of $500 million of notes" is a financing, not a sale). The answer names
   the kind, the item it was announced under (7.01, else 8.01), the words that matched as
   written, the sentence (cut to SENTENCE_CHARS) and their offsets in the section read.
"""

import re
from dataclasses import dataclass

READ_ITEMS = ("7.01", "8.01")
SKIP_ITEM = "2.02"  # an earnings release: never read
ALREADY = {"deal": "2.01", "financing": "2.03"}  # the item that already flags each kind
SENTENCE_CHARS = 300
KINDS = ("financing", "deal")  # the order rule 6 tries them in

_END = re.compile(r"[.!?:;][\"'”’)\]]*$")
_SPLIT = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[A-Z“\"])")
_EXHIBIT_99 = re.compile(r"^EX-99(?:\.(\d{1,2}))?$")

_DEAL_NOUN = (r"(?:acquisition|merger|business\s+combination|sale(?!\s+of\s+(?:\$|\d))|"
              r"divestiture|disposition|spin-?off|separation)")
DEAL_DONE = re.compile(
    r"\b(?:(?:has|have|had)\s+)?(?:complet(?:ed|es|ion\s+of)|clos(?:ed|es|ing\s+of))\b"
    r"(?:\W+[\w’'-]+){0,6}?\W+" + _DEAL_NOUN + r"\b"
    r"|\b(?:has|have)\s+(?:acquired|sold|divested)\b"
    r"|\bacquires\b", re.I)
DEAL_PENDING = re.compile(
    r"\b(?:agreements?\s+to\s+acquire|agreed\s+to\s+acquire|agrees\s+to\s+acquire|"
    r"definitive\s+(?:merger\s+)?agreement|plan\s+of\s+merger|expected\s+to\s+(?:close|be\s+"
    r"(?:completed|closed))|subject\s+to\s+(?:the\s+)?(?:customary\s+)?(?:closing|regulatory|"
    r"shareholder|stockholder)|upon\s+(?:the\s+)?(?:completion|closing)|pending)\b", re.I)
NOT_DONE = re.compile(
    r"\b(?:agree[sd]?|expects?|expected|anticipates?|anticipated|intends?|plans?|will|would|"
    r"upon|following|after|prior\s+to|pending|subject\s+to|proposed|once|until|if)\b", re.I)

_DEBT = r"(?:notes|debentures|bonds)"
_FACILITY = (r"(?:credit\s+(?:agreement|facilit(?:y|ies))|term\s+loan(?:\s+facility)?|"
             r"revolving\s+(?:credit\s+)?facility|loan\s+agreement|delayed[- ]draw)")
FINANCING = re.compile(
    r"\bpric(?:ed|es|ing\s+of)\b.{0,120}?\b" + _DEBT + r"\b"
    r"|\b(?:complet(?:ed|es)|clos(?:ed|es)|settled)\b.{0,40}?\b(?:offering|sale|issuance|"
    r"private\s+placement)\s+of\b.{0,120}?\b" + _DEBT + r"\b"
    r"|\b(?:has|have)\s+issued\b.{0,80}?\b" + _DEBT + r"\b"
    r"|\bissued\s+\$[\d.,]+.{0,80}?\b" + _DEBT + r"\b"
    r"|\bentered\s+into\b.{0,80}?\b" + _FACILITY + r"\b"
    r"|\b(?:borrowed|drew(?:\s+down)?|has\s+drawn)\b.{0,80}?\b(?:term\s+loan|credit\s+facility|"
    r"revolving|revolver)\b"
    r"|\b(?:announces|closed|closes|secured|secures|obtained|obtains|completed|completes)\b"
    r".{0,20}?\bnew\b.{0,60}?\b" + _FACILITY + r"\b", re.I | re.S)
OFFERING_PRICED = re.compile(r"\bpric(?:ed|es|ing\s+of)\b.{0,40}?\boffering\b", re.I | re.S)
NAMES_DEBT = re.compile(r"\b" + _DEBT + r"\b", re.I)
NOT_YET = re.compile(
    r"\b(?:propos(?:ed|es|al)|intends?|plans?|launch(?:es|ed)?|commenc(?:es|ed)|may(?!\s+\d)|seeks?|"
    r"would|will|expects?|if|amend(?:ment|ed|s)?)\b", re.I)


@dataclass(frozen=True)
class Announcement:
    kind: str  # "deal" | "financing"
    item: str  # "7.01" | "8.01"
    section_key: str  # where the words were read: "EX-99.1", "7.01", ...
    words: str  # the phrase that matched, as written
    sentence: str
    start: int  # the sentence's [start, end) in that section's text
    end: int

    def to_dict(self) -> dict:
        return {"kind": self.kind, "item": self.item, "section_key": self.section_key,
                "words": self.words, "sentence": self.sentence}


def sentences(text: str) -> list[tuple[int, int]]:
    """[start, end) of each sentence of a section's opening (rule 3)."""
    lines, pos = [], 0
    for line in text.split("\n"):
        a = pos + len(line) - len(line.lstrip())
        b = pos + len(line.rstrip())
        if b > a:
            lines.append((a, b))
        pos += len(line) + 1
    blocks: list[list[int]] = []
    for a, b in lines:
        if blocks and not _END.search(text[blocks[-1][0]:blocks[-1][1]]) \
                and not text[a].isupper():
            blocks[-1][1] = b
        else:
            blocks.append([a, b])
    out = []
    for a, b in blocks:
        at = a
        for m in _SPLIT.finditer(text, a, b):
            out.append((at, m.start()))
            at = m.end()
        out.append((at, b))
    return out


def _flat(s: str) -> str:
    return " ".join(s.split())


def read_sentence(sentence: str, kind: str, before: str = "") -> str | None:
    """The words that make this sentence a completed deal or a new financing (rules 4, 5);
    `before`: the section's text ahead of it."""
    flat = _flat(sentence)
    if kind == "deal":
        if DEAL_PENDING.search(flat):
            return None
        for m in DEAL_DONE.finditer(flat):
            if not NOT_DONE.search(flat[:m.start()]):
                return m.group(0)
        return None
    patterns = [FINANCING, OFFERING_PRICED] if NAMES_DEBT.search(before) else [FINANCING]
    for pattern in patterns:
        for m in pattern.finditer(flat):
            if not NOT_YET.search(flat[:m.end()]):
                return m.group(0)
    return None


def release_key(keys: list[str]) -> str | None:
    """The press release: the lowest-numbered EX-99 exhibit."""
    ranked = sorted((int(m.group(1) or 0), k) for k in keys if (m := _EXHIBIT_99.match(k)))
    return ranked[0][1] if ranked else None


def read(items: list[str], heads: dict[str, str]) -> list[Announcement]:
    """What an 8-K with these items announces, from its sections' openings (section_key →
    text): at most one Announcement per kind, none for an 8-K rule 1 leaves out."""
    if SKIP_ITEM in items or not any(i in items for i in READ_ITEMS):
        return []
    item = next(i for i in READ_ITEMS if i in items)
    order = [k for k in (release_key(list(heads)), *READ_ITEMS) if k and k in heads]
    out: list[Announcement] = []
    for kind in KINDS:
        if ALREADY[kind] in items:
            continue
        found = None
        for key in order:
            text = heads[key]
            for a, b in sentences(text):
                words = read_sentence(text[a:b], kind, text[:a])
                if words:
                    found = Announcement(kind, item, key, words,
                                         _cut(_flat(text[a:b])), a, b)
                    break
            if found:
                break
        if found:
            out.append(found)
    return out


def _cut(s: str) -> str:
    if len(s) <= SENTENCE_CHARS:
        return s
    cut = s[:SENTENCE_CHARS]
    space = cut.rfind(" ")
    return (cut[:space] if space > SENTENCE_CHARS // 2 else cut).rstrip(" ,;:") + " …"
