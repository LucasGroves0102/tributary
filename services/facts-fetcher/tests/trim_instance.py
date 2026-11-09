"""Build a trimmed XBRL instance fixture from a real one (a filing's `.xml`, fetched once).

    python -I services/facts-fetcher/tests/trim_instance.py IN.xml OUT.xml.gz 'REGEX'

Keeps the root element with its namespace declarations, the numeric facts whose concept (as
written, prefix:local) matches REGEX, and the contexts and units they use. Used once per fixture;
not part of the test run."""

import gzip
import re
import sys

src, out, pattern = sys.argv[1], sys.argv[2], re.compile(sys.argv[3])
text = open(src, encoding="utf-8").read()
root = re.search(r"<(\w+:)?xbrl\b[^>]*>", text)
assert root, "no xbrl root"
prefix = root.group(1) or ""


def blocks(kind: str) -> dict[str, str]:
    """id -> the whole element, for every context or unit."""
    found = re.findall(rf'(<{prefix}{kind}\b[^>]*\bid="([^"]+)".*?</{prefix}{kind}>)', text, re.S)
    return {element_id: block for block, element_id in found}


contexts = blocks("context")
units = blocks("unit")
facts = []
for m in re.finditer(r"<([\w-]+:\w+)\b([^>]*\bcontextRef=\"([^\"]+)\"[^>]*)>([^<]*)</\1>", text):
    concept, attrs, ctx, _ = m.groups()
    if "unitRef" in attrs and pattern.search(concept):
        unit = re.search(r'unitRef="([^"]+)"', attrs)
        assert unit
        facts.append((m.group(0), ctx, unit.group(1)))
used_ctx = sorted({c for _, c, _ in facts})
used_units = sorted({u for _, _, u in facts})
doc = [root.group(0)]
doc += [contexts[c] for c in used_ctx]
doc += [units[u] for u in used_units]
doc += [f for f, _, _ in facts]
doc.append(f"</{prefix}xbrl>\n")
with gzip.open(out, "wt", encoding="utf-8") as f:
    f.write("\n".join(doc))
print(f"{len(facts)} facts, {len(used_ctx)} contexts, {len(used_units)} units -> {out}")
