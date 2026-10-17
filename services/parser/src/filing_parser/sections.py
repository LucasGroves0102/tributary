"""Split a filing's primary document into Part/Item sections. Pure, no I/O.

The algorithm is specified precisely so the Rust rewrite (week 4) can match it exactly:

1. html_to_text: drop <script>, <style>, inline-XBRL <ix:header> and display:none elements;
   a newline after every block element, a space after every table cell; collapse runs of
   spaces/tabs/nbsp/zero-width to one space, trim each line, drop empty lines.
2. Headings are lines starting with "Part <roman>" or "Item <n>[letter]" / "Item <n>.<nn>".
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
"""

import re
from dataclasses import dataclass

from lxml import html

PARSER_VERSION = "python-2"

_BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
_CELL = {"td", "th"}
_DROP = (
    "//script | //style | //*[local-name()='header']"
    " | //*[contains(translate(@style, ' ', ''), 'display:none')]"
)
_SPACES = re.compile(r"[ \t\xa0​  ]+")
_HEADING = re.compile(
    r"^(?:part\s+(?P<part>iv|i{1,3})\b|item\s+(?P<item>\d{1,2}\.\d{2}|\d{1,2}[a-z]?)\b)"
    r"[ .:\-–—]*(?P<rest>.*)$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class Section:
    key: str          # 10-K: "1A"; 10-Q: "II-1A"; 8-K: "2.02"
    part: str | None  # "I", "II", ... when known
    item: str
    title: str
    text: str
    char_start: int   # offsets into html_to_text(document)
    char_end: int


def html_to_text(raw: bytes) -> str:
    doc = html.fromstring(raw)
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
    lines = (_SPACES.sub(" ", line).strip() for line in doc.text_content().split("\n"))
    return "\n".join(line for line in lines if line)


@dataclass
class _Heading:
    start: int
    part: str | None   # Part heading: its numeral; Item heading: unused until the body pass
    item: str | None   # None for a Part heading
    title: str
    inline_title: str = ""  # text after the item number on the heading's own line
    key: str = ""
    span: int = 0


def find_sections(text: str, form: str) -> list[Section]:
    headings = list(_headings(text))
    body = _body(headings, form)
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
        sections.append(Section(h.key, h.part, h.item, h.title, text[h.start:end].strip(),
                                h.start, end))
    return sections


def _headings(text: str):
    for m in _HEADING.finditer(text):
        if m.group("part"):
            yield _Heading(m.start(), m.group("part").upper(), None, "")
            continue
        inline = title = m.group("rest").strip()
        if not title:  # title on the next line, as in many tables of contents
            nxt = text.find("\n", m.end() + 1)
            title = text[m.end() + 1 : nxt if nxt != -1 else len(text)].strip()
        yield _Heading(m.start(), None, m.group("item").upper(), title[:200], inline)


def _body(headings: list[_Heading], form: str) -> list[_Heading]:
    """Headings after the table of contents, with page headers dropped and keys assigned."""
    # rule 3: find where the body starts
    seen, part, start = set(), None, 0
    for i, h in enumerate(headings):
        if h.item is None:
            part = h.part
            continue
        key = _key(form, part, h.item)
        if key in seen:
            start = i
            if i > 0 and headings[i - 1].item is None and h.start - headings[i - 1].start <= 50:
                start = i - 1
            break
        seen.add(key)

    # rule 4: walk the body
    body: list[_Heading] = []
    part, current = None, None
    for h in headings[start:]:
        if h.item is None:
            if h.part == part:
                continue
            part = h.part
            body.append(h)
            continue
        h.part = part or ("I" if form.startswith("10-Q") else None)
        h.key = _key(form, h.part, h.item)
        if current and h.key == current.key and h.inline_title in ("", current.inline_title):
            continue
        current = h
        body.append(h)
    return body


def parse(raw: bytes, form: str) -> list[Section]:
    return find_sections(html_to_text(raw), form)


def _key(form: str, part: str | None, item: str) -> str:
    # 10-Q item numbers restart in each Part; 10-K and 8-K item numbers are unique
    if form.startswith("10-Q"):
        return f"{part or '?'}-{item}"
    return item
