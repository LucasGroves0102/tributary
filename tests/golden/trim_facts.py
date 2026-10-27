"""Trim a full companyfacts payload to the golden-test fixture.

Keeps the us-gaap tags that `silver.metric_tags` maps (read from the migrations) and the facts
whose period ends in the two fiscal years up to FY_END (four quarters and their year-ago
quarters, the year-ago balance sheet). Facts filed after the 10-K are kept, as the SEC serves
them: the test checks what the pipeline makes of the real payload, later filings included.

    python -I tests/golden/trim_facts.py FULL.json FY_END OUT.json [FULL.json FY_END OUT.json ...]
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
WINDOW = timedelta(days=760)


def mapped_tags() -> set[str]:
    tags: set[str] = set()
    for sql in sorted(MIGRATIONS.glob("*.sql")):
        # (metric, tag, priority) rows; segment tags are QNames ("us-gaap:...") and don't match
        tags.update(re.findall(r"\('[a-z_]+',\s*'(\w+)',\s*\d+\)", sql.read_text()))
    return tags


def trim(payload: dict, fy_end: date) -> dict:
    keep = mapped_tags()
    first = fy_end - WINDOW
    facts: dict = {}
    for tag, concept in payload.get("facts", {}).get("us-gaap", {}).items():
        if tag not in keep:
            continue
        units = {}
        for unit, rows in concept["units"].items():
            rows = [r for r in rows if first <= date.fromisoformat(r["end"]) <= fy_end]
            if rows:
                units[unit] = rows
        if units:
            facts[tag] = {"label": concept.get("label"), "units": units}
    return {"cik": payload.get("cik"), "entityName": payload.get("entityName"),
            "facts": {"us-gaap": facts} if facts else {}}


def main(argv: list[str]) -> int:
    for i in range(0, len(argv), 3):
        src, fy_end, out = Path(argv[i]), date.fromisoformat(argv[i + 1]), Path(argv[i + 2])
        trimmed = trim(json.loads(src.read_bytes()), fy_end)
        out.write_text(json.dumps(trimmed, separators=(",", ":"), sort_keys=True) + "\n")
        n = sum(len(r) for c in trimmed["facts"].get("us-gaap", {}).values()
                for r in c["units"].values())
        print(f"{out}: {len(trimmed['facts'].get('us-gaap', {}))} tags, {n} facts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
