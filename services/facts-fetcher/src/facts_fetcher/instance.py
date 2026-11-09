"""Numeric facts from an XBRL instance document (the filing's `.xml`), streamed.

Every XBRL filing has an instance: the `<prefix>-<date>.xml` filed before inline XBRL, and the
`<primary document>_htm.xml` EDGAR extracts from an inline XBRL filing. It holds the facts of all
the filing's documents, so it is what to read when the primary document's inline XBRL lacks the
statements (Clorox's 10-K carries them in its annual report exhibit) or no document is stored
(filings before the crawl's years). Same output as ixbrl.py (Fact, Extract) and the same rules,
minus the display transforms an instance doesn't have (EXTRACTOR_VERSION is ixbrl.py's):

1. Contexts: id -> entity CIK, period, explicit members (axis QName -> member QName, as written)
   and typed members (axis -> "typed:<value>").
2. Units: id -> measure without its prefix; a divide is "numerator/denominator".
3. Facts: elements with a contextRef and a unitRef (numeric items). Nil facts are skipped. The
   text is the value as reported (already scaled and signed); one that isn't a decimal number is
   skipped and counted. Concepts are named prefix:local with the standard taxonomies' usual
   prefixes (us-gaap, ifrs-full, dei, srt, ...) whatever prefix the file binds, and a filer's own
   namespace by the prefix it declares (nvda:...).
4. Duplicates: the same concept, unit, period and dimensions keep one value; differing values
   (a figure rounded in the text beside the exact one in a table) are counted as conflicts and the
   more precise one (`decimals`) is kept, as companyfacts does. ixbrl.py keeps the first of them
   (changing that is an EXTRACTOR_VERSION bump: every stored document re-extracted).

Parsed with the standard library's expat-based iterparse (no DTDs or external entities are
fetched; expat 2.4+ refuses entity expansion bombs); each top-level element is dropped once read,
so memory holds the contexts and facts, not the document.
"""

import gzip
import io
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from facts_fetcher.ixbrl import Context, Extract, Fact

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# standard namespaces -> the prefix every SEC filer's inline XBRL uses for them
_STANDARD = [
    (re.compile(r"^https?://(xbrl\.)?fasb\.org/us-gaap/"), "us-gaap"),
    (re.compile(r"^https?://xbrl\.ifrs\.org/taxonomy/[^/]+/ifrs-full$"), "ifrs-full"),
    (re.compile(r"^https?://xbrl\.sec\.gov/dei/"), "dei"),
    (re.compile(r"^https?://(xbrl\.)?fasb\.org/srt/"), "srt"),
    (re.compile(r"^https?://xbrl\.sec\.gov/country/"), "country"),
    (re.compile(r"^https?://xbrl\.sec\.gov/currency/"), "currency"),
    (re.compile(r"^https?://xbrl\.sec\.gov/exch/"), "exch"),
    (re.compile(r"^https?://xbrl\.sec\.gov/stpr/"), "stpr"),
    (re.compile(r"^https?://xbrl\.sec\.gov/ecd/"), "ecd"),
    (re.compile(r"^https?://xbrl\.sec\.gov/ffd/"), "ffd"),
]
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _split(tag: str) -> tuple[str, str]:
    """'{uri}local' -> (uri, local)."""
    if tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return uri, local
    return "", tag


def _measure(text: str) -> str:
    return text.strip().rsplit(":", 1)[-1]


def _precision(decimals: str | None) -> float:
    """Higher is more precise: INF, then the decimals attribute (-3 beats -6)."""
    if decimals is None:
        return float("-inf")
    if decimals.strip().upper() == "INF":
        return float("inf")
    try:
        return float(decimals)
    except ValueError:
        return float("-inf")


class _Reader:
    def __init__(self) -> None:
        self.prefixes: dict[str, str] = {}   # namespace URI -> prefix for concept names
        self.contexts: dict[str, Context] = {}
        self.units: dict[str, str] = {}
        self.raw: list[tuple[str, str, str | None, str | None, str]] = []
        self.out = Extract()
        self.ciks: dict[str, int] = {}

    def declare(self, prefix: str, uri: str) -> None:
        for pattern, standard in _STANDARD:
            if pattern.match(uri):
                prefix = standard
                break
        self.prefixes.setdefault(uri, prefix)

    def qname(self, tag: str) -> str:
        uri, local = _split(tag)
        prefix = self.prefixes.get(uri)
        return f"{prefix}:{local}" if prefix else local

    def context(self, el: ET.Element) -> None:
        cid = el.get("id")
        if not cid:
            return
        cik = start = end = instant = None
        members: list[tuple[str, str]] = []
        for sub in el.iter():
            uri, name = _split(sub.tag)
            if uri == XBRLI and name == "identifier":
                cik = (sub.text or "").strip()
            elif uri == XBRLI and name == "startDate":
                start = (sub.text or "").strip()
            elif uri == XBRLI and name == "endDate":
                end = (sub.text or "").strip()
            elif uri == XBRLI and name == "instant":
                instant = (sub.text or "").strip()
            elif uri == XBRLDI and name == "explicitMember" and sub.get("dimension"):
                members.append((sub.get("dimension") or "", (sub.text or "").strip()))
            elif uri == XBRLDI and name == "typedMember" and sub.get("dimension"):
                value = " ".join("".join(sub.itertext()).split())
                members.append((sub.get("dimension") or "", "typed:" + value))
        if cik:
            self.ciks[cik] = self.ciks.get(cik, 0) + 1
        self.contexts[cid] = Context(cik=cik, start=start, end=end, instant=instant,
                                     members=tuple(sorted(members)))

    def unit(self, el: ET.Element) -> None:
        uid = el.get("id")
        if not uid:
            return
        num: list[str] = []
        den: list[str] = []
        measures: list[str] = []
        for sub in el.iter():
            uri, name = _split(sub.tag)
            if uri != XBRLI:
                continue
            if name == "unitNumerator":
                num = [_measure(m.text or "") for m in sub.iter(f"{{{XBRLI}}}measure")]
            elif name == "unitDenominator":
                den = [_measure(m.text or "") for m in sub.iter(f"{{{XBRLI}}}measure")]
        if num:
            self.units[uid] = "*".join(num) + "/" + "*".join(den)
        else:
            measures = [_measure(m.text or "") for m in el.iter(f"{{{XBRLI}}}measure")]
            self.units[uid] = "*".join(measures)

    def item(self, el: ET.Element) -> None:
        ctx, unit = el.get("contextRef"), el.get("unitRef")
        if ctx is None or unit is None:
            return  # a non-numeric item, a tuple, a footnote link
        if (el.get(f"{{{XSI}}}nil") or "").lower() == "true":
            return
        self.raw.append((self.qname(el.tag), ctx, unit, el.get("decimals"),
                         (el.text or "").strip()))

    def result(self) -> Extract:
        seen: dict[tuple, int] = {}  # key -> index in out.facts
        for concept, ctx_id, unit_id, decimals, text in self.raw:
            ctx = self.contexts.get(ctx_id)
            if ctx is None:
                self.out.missing_context += 1
                continue
            if not _NUMBER.match(text):
                self.out.unsupported += 1
                continue
            try:
                value = Decimal(text)
            except InvalidOperation:
                self.out.unsupported += 1
                continue
            fact = Fact(concept=concept, value=value, unit=self.units.get(unit_id or ""),
                        decimals=decimals, start=ctx.start, end=ctx.end, instant=ctx.instant,
                        members=ctx.members)
            key = fact.key()
            if key in seen:
                kept = self.out.facts[seen[key]]
                if kept.value != value:
                    self.out.conflicts += 1
                    if _precision(decimals) > _precision(kept.decimals):
                        self.out.facts[seen[key]] = fact
                continue
            seen[key] = len(self.out.facts)
            self.out.facts.append(fact)
        self.out.contexts = len(self.contexts)
        self.out.has_header = bool(self.contexts)
        if self.ciks:
            self.out.entity_cik = max(self.ciks.items(), key=lambda kv: (kv[1], kv[0]))[0]
        return self.out


def extract_stream(stream: io.BufferedIOBase | gzip.GzipFile) -> Extract:
    """Facts from an instance document read from a binary stream."""
    reader = _Reader()
    depth = 0
    for event, payload in ET.iterparse(stream, events=("start-ns", "start", "end")):
        if event == "start-ns":
            prefix, uri = payload  # type: ignore[misc]
            reader.declare(prefix, uri)
            continue
        el: ET.Element = payload  # type: ignore[assignment]
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth != 1:  # only the root's children are whole items, contexts and units
            continue
        uri, name = _split(el.tag)
        if uri == XBRLI and name == "context":
            reader.context(el)
        elif uri == XBRLI and name == "unit":
            reader.unit(el)
        elif uri not in (XBRLI, "http://www.xbrl.org/2003/linkbase"):
            reader.item(el)
        el.clear()
    return reader.result()


def extract_bytes(content: bytes) -> Extract:
    return extract_stream(io.BytesIO(content))


def extract_gzip(content_gzip: bytes) -> Extract:
    """Facts from a gzip-compressed instance as stored in bronze.filing_instances."""
    with gzip.GzipFile(fileobj=io.BytesIO(content_gzip)) as f:
        return extract_stream(f)
