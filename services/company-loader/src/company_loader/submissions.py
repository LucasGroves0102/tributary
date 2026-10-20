"""Parse the SEC's bulk submissions.zip: per CIK, CIK##########.json (metadata and recent
filings) plus CIK##########-submissions-NNN.json pages with older filings. Pure, no I/O."""

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from tributary_sec import acceptance_time

ENTRY = re.compile(r"^CIK(\d{10})(-submissions-\d+)?\.json$")

# bronze.submission_filings columns, in COPY order
FILING_COLUMNS = (
    "accession_no", "cik", "form", "filing_date", "accepted_at", "report_date",
    "primary_document", "primary_doc_description", "items", "file_number", "size_bytes",
    "is_xbrl", "is_inline_xbrl",
)


@dataclass(frozen=True)
class Submission:
    cik: str
    content_sha256: str
    metadata: dict  # the main JSON without "filings"
    filings: list[tuple]  # FILING_COLUMNS rows


def entry_cik(name: str) -> tuple[str, bool] | None:
    """(cik, is_page) for a submissions entry name, None for anything else."""
    m = ENTRY.match(name)
    return (m.group(1), m.group(2) is not None) if m else None


def content_hash(blobs: list[bytes]) -> str:
    """Over a CIK's main JSON and its pages, in that order."""
    h = hashlib.sha256()
    for b in blobs:
        h.update(len(b).to_bytes(8, "big"))
        h.update(b)
    return h.hexdigest()


def parse(cik: str, blobs: list[bytes], digest: str) -> Submission:
    """blobs: the main JSON first, then its pages."""
    main = json.loads(blobs[0])
    filings = main.pop("filings", {})
    rows = list(filing_rows(cik, filings.get("recent", {})))
    for page in blobs[1:]:
        rows.extend(filing_rows(cik, json.loads(page)))
    return Submission(cik, digest, main, rows)


def filing_rows(cik: str, cols: dict[str, list]) -> Iterator[tuple]:
    """Filings are column arrays: {"accessionNumber": [...], "form": [...], ...}."""
    def col(name: str) -> list:
        return cols.get(name) or [None] * n

    n = len(cols.get("accessionNumber") or [])
    for acc, form, filed, accepted, report, doc, desc, items, file_no, size, xbrl, ixbrl in zip(
        col("accessionNumber"), col("form"), col("filingDate"), col("acceptanceDateTime"),
        col("reportDate"), col("primaryDocument"), col("primaryDocDescription"), col("items"),
        col("fileNumber"), col("size"), col("isXBRL"), col("isInlineXBRL"), strict=True,
    ):
        if not acc or not form or not filed:
            continue
        yield (
            acc, cik, form, date.fromisoformat(filed), acceptance_time(accepted),
            _date(report), doc or None, desc or None, items or None, file_no or None,
            size if isinstance(size, int) else None, _bool(xbrl), _bool(ixbrl),
        )


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _bool(v: object) -> bool | None:
    return None if v is None or v == "" else bool(v)
