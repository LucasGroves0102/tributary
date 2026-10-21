"""Numeric facts from an inline XBRL (iXBRL) document, streamed.

A 10-K or 10-Q primary document filed since 2019-2021 (the SEC's inline XBRL phase-in) is
itself the XBRL instance: `ix:header` holds the contexts (entity, period, dimensions) and units,
and every `ix:nonFraction` in the body is a numeric fact pointing at one of each. Unlike the
companyfacts API, this keeps the dimensions (segments, geography, products).

The document is fed to an HTML tokenizer in chunks, so memory holds the contexts and facts,
never the whole document. Rules (EXTRACTOR_VERSION; bump it when they change, the loader then
re-extracts every stored document):

1. Contexts: id → entity CIK, period (startDate/endDate or instant), explicit members
   (axis QName → member QName) and typed members (axis → "typed:<value>").
2. Units: id → measure without its prefix (`iso4217:USD` → USD); a divide is
   "numerator/denominator" (USD/shares).
3. Facts: `ix:nonFraction` with a known context. Nil facts are skipped. The displayed text is
   transformed by its `format` (num-dot-decimal, num-comma-decimal, zero-dash/fixed-zero, or no
   format = plain decimal; any other format is skipped and counted), then multiplied by
   10^scale and negated when sign="-". Values stay exact decimals.
4. Duplicates: the same concept, unit, period and dimensions reported more than once (a
   statement and its note) keep the first value; differing values are counted as conflicts.
5. Element and attribute names are matched by local name, case-insensitively, so any prefix
   bound to the ix/xbrli/xbrldi namespaces works.
"""

import gzip
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

EXTRACTOR_VERSION = 1
CHUNK = 1 << 20

_ZERO_FORMATS = {"zerodash", "zero-dash", "fixed-zero", "fixedzero", "numdash"}
_DOT_FORMATS = {"num-dot-decimal", "numdotdecimal", "numcommadot", "num-dot-decimal-apos",
                "numspacedot", "num-space-dot"}
_COMMA_FORMATS = {"num-comma-decimal", "numcommadecimal", "numdotcomma", "numspacecomma",
                  "num-space-comma"}
_NUMBER = re.compile(r"\d+(\.\d+)?")


@dataclass(frozen=True)
class Context:
    cik: str | None
    start: str | None
    end: str | None
    instant: str | None
    members: tuple[tuple[str, str], ...]  # (axis, member), sorted by axis


@dataclass(frozen=True)
class Fact:
    concept: str               # e.g. us-gaap:Revenues
    value: Decimal
    unit: str | None           # USD, shares, USD/shares, pure
    decimals: str | None       # as reported: "-6", "INF"
    start: str | None          # ISO dates; start/end for durations, instant otherwise
    end: str | None
    instant: str | None
    members: tuple[tuple[str, str], ...]

    def key(self) -> tuple:
        return (self.concept, self.unit, self.start, self.end, self.instant, self.members)


@dataclass
class Extract:
    facts: list[Fact] = field(default_factory=list)
    contexts: int = 0
    entity_cik: str | None = None  # the most common entity identifier
    has_header: bool = False
    unsupported: int = 0       # facts with a format we don't transform
    conflicts: int = 0         # duplicates with a different value
    missing_context: int = 0


def local(name: str) -> str:
    return name.rsplit(":", 1)[-1].lower()


def strip_prefix(qname: str) -> str:
    return qname.rsplit(":", 1)[-1]


def parse_value(text: str, fmt: str | None, scale: str | None, sign: str | None) -> Decimal | None:
    """The fact's value from its displayed text, or None if the format isn't supported."""
    kind = local(fmt) if fmt else None
    t = text.strip()
    if kind in _ZERO_FORMATS:
        value = Decimal(0)
    elif kind is None or kind in _DOT_FORMATS or kind in _COMMA_FORMATS:
        if kind in _COMMA_FORMATS:
            t = re.sub(r"[.\s' ]", "", t).replace(",", ".")
        elif kind in _DOT_FORMATS:
            t = re.sub(r"[,\s' ]", "", t)
        else:
            t = re.sub(r"\s", "", t)
        if not t and kind is not None:
            return None
        if t in ("-", "—", "–") and kind is not None:  # dashes shown for zero
            value = Decimal(0)
        else:
            if not _NUMBER.fullmatch(t):
                return None
            try:
                value = Decimal(t)
            except InvalidOperation:
                return None
    else:
        return None
    if scale:
        try:
            value = value.scaleb(int(scale))
        except ValueError:
            return None
    if sign == "-":
        value = -value
    return value


def plain(value: Decimal) -> str:
    """Exact decimal string without exponent or trailing zeros: 1.2E+10 → 12000000000."""
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, Context] = {}
        self.units: dict[str, str] = {}
        self.out = Extract()
        self._seen: dict[tuple, Decimal] = {}
        self._ciks: dict[str, int] = {}
        # context being read
        self._ctx_id: str | None = None
        self._ctx: dict = {}
        self._field: str | None = None  # element whose text we collect in a context/unit
        self._axis: str | None = None
        self._typed_depth = 0
        self._text: list[str] = []
        # unit being read
        self._unit_id: str | None = None
        self._unit: dict[str, list[str]] = {}
        self._unit_part = "measure"
        # numeric facts being read (they can nest)
        self._facts: list[tuple[dict[str, str | None], list[str]]] = []
        # facts read, resolved against contexts and units at the end (the header may follow)
        self._raw: list[tuple[dict[str, str | None], str]] = []

    # tokenizer callbacks ---------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = local(tag)
        a = {k.lower(): v for k, v in attrs}
        if name == "header":
            self.out.has_header = True
        elif name == "context" and ":" in tag:
            self._ctx_id = a.get("id")
            self._ctx = {"members": []}
        elif self._ctx_id is not None:
            self._context_start(name, a)
        elif name == "unit" and ":" in tag:
            self._unit_id = a.get("id")
            self._unit = {"measure": [], "unitnumerator": [], "unitdenominator": []}
            self._unit_part = "measure"
        elif self._unit_id is not None:
            if name in ("unitnumerator", "unitdenominator"):
                self._unit_part = name
            elif name == "measure":
                self._field, self._text = "measure", []
        elif name == "nonfraction":
            self._facts.append((a, []))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        name = local(tag)
        if self._ctx_id is not None:
            if name == "context":
                self._context_end()
            else:
                self._context_field_end(name)
        elif self._unit_id is not None:
            if name == "measure" and self._field == "measure":
                self._unit[self._unit_part].append(strip_prefix("".join(self._text).strip()))
                self._field = None
            elif name in ("unitnumerator", "unitdenominator"):
                self._unit_part = "measure"
            elif name == "unit":
                u = self._unit
                if u["unitnumerator"]:
                    unit = "*".join(u["unitnumerator"]) + "/" + "*".join(u["unitdenominator"])
                else:
                    unit = "*".join(u["measure"])
                self.units[self._unit_id] = unit
                self._unit_id = None
        elif name == "nonfraction" and self._facts:
            attrs, parts = self._facts.pop()
            text = "".join(parts)
            if self._facts:  # a nested fact's text is also its parent's
                self._facts[-1][1].append(text)
            self._fact(attrs, text)

    def handle_data(self, data: str) -> None:
        if self._field is not None or self._typed_depth:
            self._text.append(data)
        if self._facts:
            self._facts[-1][1].append(data)

    # contexts --------------------------------------------------------------------------------

    def _context_start(self, name: str, a: dict[str, str | None]) -> None:
        if self._typed_depth:
            self._typed_depth += 1
            return
        if name in ("identifier", "startdate", "enddate", "instant"):
            self._field, self._text = name, []
        elif name == "explicitmember":
            self._field, self._text, self._axis = name, [], a.get("dimension")
        elif name == "typedmember":
            self._axis, self._text, self._typed_depth = a.get("dimension"), [], 1

    def _context_field_end(self, name: str) -> None:
        if self._typed_depth:
            self._typed_depth -= 1
            if self._typed_depth == 0 and self._axis:
                value = " ".join("".join(self._text).split())
                self._ctx["members"].append((self._axis, "typed:" + value))
            return
        if name != self._field:
            return
        value = "".join(self._text).strip()
        if name == "explicitmember":
            if self._axis:
                self._ctx["members"].append((self._axis, value))
        else:
            self._ctx[name] = value
        self._field = None

    def _context_end(self) -> None:
        c = self._ctx
        cik = c.get("identifier")
        if cik:
            self._ciks[cik] = self._ciks.get(cik, 0) + 1
        assert self._ctx_id is not None
        self.contexts[self._ctx_id] = Context(
            cik=cik, start=c.get("startdate"), end=c.get("enddate"), instant=c.get("instant"),
            members=tuple(sorted(c["members"])))
        self._ctx_id = None
        self._field = None

    # facts -----------------------------------------------------------------------------------

    def _fact(self, a: dict[str, str | None], text: str) -> None:
        if (a.get("xsi:nil") or "").lower() == "true" or not a.get("name"):
            return
        self._raw.append((a, text))

    def _resolve(self, a: dict[str, str | None], text: str) -> None:
        name = a["name"]
        assert name is not None
        ctx = self.contexts.get(a.get("contextref") or "")
        if ctx is None:
            self.out.missing_context += 1
            return
        value = parse_value(text, a.get("format"), a.get("scale"), a.get("sign"))
        if value is None:
            self.out.unsupported += 1
            return
        unit = self.units.get(a.get("unitref") or "")
        fact = Fact(concept=name, value=value, unit=unit, decimals=a.get("decimals"),
                    start=ctx.start, end=ctx.end, instant=ctx.instant, members=ctx.members)
        key = fact.key()
        if key in self._seen:
            if self._seen[key] != value:
                self.out.conflicts += 1
            return
        self._seen[key] = value
        self.out.facts.append(fact)

    def result(self) -> Extract:
        for a, text in self._raw:
            self._resolve(a, text)
        self._raw.clear()
        self.out.contexts = len(self.contexts)
        if self._ciks:
            self.out.entity_cik = max(self._ciks.items(), key=lambda kv: (kv[1], kv[0]))[0]
        return self.out


def extract_chunks(chunks: Iterable[str]) -> Extract:
    """Facts from a document given as text chunks. Facts are resolved against the contexts and
    units once the whole document is read, so the header may come anywhere."""
    p = _Parser()
    for chunk in chunks:
        p.feed(chunk)
    p.close()
    return p.result()


def _chunks(stream: io.BufferedIOBase | gzip.GzipFile, encoding: str = "utf-8") -> Iterator[str]:
    reader = io.TextIOWrapper(stream, encoding=encoding, errors="replace")  # type: ignore[arg-type]
    while chunk := reader.read(CHUNK):
        yield chunk


def extract_gzip(content_gzip: bytes) -> Extract:
    """Facts from a gzip-compressed document as stored in bronze.filing_documents, decompressed
    a chunk at a time."""
    with gzip.GzipFile(fileobj=io.BytesIO(content_gzip)) as f:
        return extract_chunks(_chunks(f))
