"""Build a trimmed iXBRL fixture from a real filing, streamed (never held whole in memory).

    python -I services/facts-fetcher/tests/trim_ixbrl.py URL OUT.htm.gz   # via sec_client
    python -I services/facts-fetcher/tests/trim_ixbrl.py FILE.htm.gz OUT.htm.gz

Keeps the numeric facts of the segment metric concepts (CONCEPTS) that are consolidated or
dimensioned only by axes in AXES, the contexts and units they use, and nothing else, as a small
iXBRL document the extractor reads like the original. Used once per fixture; not part of the
test run (SEC_USER_AGENT must be set for a URL)."""

import codecs
import gzip
import os
import re
import sys
from html.parser import HTMLParser

CONCEPTS = {
    "us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "us-gaap:SalesRevenueNet",
    "us-gaap:RevenuesNetOfInterestExpense", "us-gaap:OperatingIncomeLoss", "us-gaap:GrossProfit",
    "us-gaap:SegmentReportingInformationOperatingIncomeLoss",
}
AXES = {"us-gaap:StatementBusinessSegmentsAxis", "srt:StatementGeographicalAxis",
        "srt:ProductOrServiceAxis", "srt:ConsolidationItemsAxis"}
MEMBER = re.compile(r'dimension="([^"]+)"')


class Capture(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.contexts: dict[str, str] = {}
        self.units: dict[str, str] = {}
        self.facts: list[tuple[str, str, str, str]] = []  # name, context, unit, raw
        self._cur: list[str] | None = None
        self._kind = ""
        self._id = ""
        self._depth = 0
        self._fact: tuple[dict, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text() or ""
        a = dict(attrs)
        local = tag.rsplit(":", 1)[-1]
        if self._cur is not None:
            self._cur.append(raw)
            if tag.rsplit(":", 1)[-1] == self._kind:
                self._depth += 1
        elif local in ("context", "unit") and ":" in tag:
            self._cur, self._kind, self._id, self._depth = [raw], local, a.get("id", ""), 1
        elif local == "nonfraction" and self._fact is None:
            self._fact = (a, [raw])
        elif self._fact is not None:
            self._fact[1].append(raw)

    def handle_startendtag(self, tag, attrs):
        raw = self.get_starttag_text() or ""
        if self._cur is not None:
            self._cur.append(raw)
        elif self._fact is not None:
            self._fact[1].append(raw)

    def handle_endtag(self, tag):
        end = f"</{tag}>"
        local = tag.rsplit(":", 1)[-1]
        if self._cur is not None:
            self._cur.append(end)
            if local == self._kind:
                self._depth -= 1
                if self._depth == 0:
                    (self.contexts if self._kind == "context" else self.units)[self._id] = \
                        "".join(self._cur)
                    self._cur = None
        elif self._fact is not None:
            self._fact[1].append(end)
            if local == "nonfraction":
                a, parts = self._fact
                self.facts.append((a.get("name", ""), a.get("contextref", ""),
                                   a.get("unitref", ""), "".join(parts)))
                self._fact = None

    def handle_data(self, data):
        if self._cur is not None:
            self._cur.append(data)
        elif self._fact is not None:
            self._fact[1].append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")


def chunks(src: str):
    if src.startswith("https://"):
        from tributary_sec import sec_client
        with sec_client(os.environ["SEC_USER_AGENT"]) as client, \
                client.stream("GET", src) as resp:
            resp.raise_for_status()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            for b in resp.iter_bytes(1 << 16):
                yield decoder.decode(b)
    else:
        with gzip.open(src, "rt", encoding="utf-8", errors="replace") as f:
            while c := f.read(1 << 16):
                yield c


def main(src: str, out: str) -> None:
    p = Capture()
    for c in chunks(src):
        p.feed(c)
    p.close()
    keep = []
    for name, ctx, unit, raw in p.facts:
        context = p.contexts.get(ctx)
        if name not in CONCEPTS or context is None:
            continue
        axes = MEMBER.findall(context)
        if all(a in AXES for a in axes) and len(axes) <= 2:
            keep.append((ctx, unit, raw))
    ctxs = sorted({c for c, _, _ in keep})
    units = sorted({u for _, u, _ in keep if u})
    doc = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'
        ' xmlns:xbrli="http://www.xbrl.org/2003/instance"'
        ' xmlns:xbrldi="http://xbrl.org/2006/xbrldi"><body>\n'
        '<div style="display:none"><ix:header><ix:resources>\n'
        + "\n".join(p.contexts[c] for c in ctxs) + "\n"
        + "\n".join(p.units[u] for u in units if u in p.units) + "\n"
        "</ix:resources></ix:header></div>\n<table>\n"
        + "\n".join(f"<tr><td>{raw}</td></tr>" for _, _, raw in keep)
        + "\n</table></body></html>\n")
    with gzip.open(out, "wt", encoding="utf-8") as f:
        f.write(doc)
    print(f"{len(keep)} facts, {len(ctxs)} contexts, {len(doc)} bytes", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
