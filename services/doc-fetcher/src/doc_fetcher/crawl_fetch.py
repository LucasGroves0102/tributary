"""Fetch one crawl target's files from EDGAR (network only, no database).

10-K / 10-Q: the primary document named in the submissions history, one request. 8-K, or a
filing whose primary document is unknown or not HTML: the filing index first (exhibits and the
primary document's name), like doc-fetcher. Bodies are zstd-compressed and hashed while they
stream in (tributary_bodies.Compressing), so a worker holds the compressed bytes only, and files
over `max_bytes` are refused without being read to the end.
"""

from dataclasses import dataclass, field
from datetime import date

import httpx

from doc_fetcher.index import (
    EXHIBITS,
    SEC_BASE,
    Exhibit,
    FilingIndex,
    index_url,
    parse_exhibits,
    parse_index,
)
from tributary_bodies import Body, Compressing

CHUNK = 1 << 16


@dataclass(frozen=True)
class Target:
    accession_no: str
    cik: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str | None
    attempts: int = 0


@dataclass
class Fetched:
    index: FilingIndex
    document: Body
    exhibits: list[tuple[Exhibit, Body]] = field(default_factory=list)
    requests: int = 0

    @property
    def files(self) -> int:
        return 1 + len(self.exhibits)

    @property
    def raw_bytes(self) -> int:
        return self.document.size + sum(b.size for _, b in self.exhibits)

    @property
    def stored_bytes(self) -> int:
        return len(self.document.data) + sum(len(b.data) for _, b in self.exhibits)


class Unusable(Exception):
    """The filing can't be stored as is (too large, empty, no primary document): skip it."""


def archive_url(cik: str, accession_no: str, name: str) -> str:
    return f"{SEC_BASE}/Archives/edgar/data/{int(cik)}/{accession_no.replace('-', '')}/{name}"


def needs_index(target: Target) -> bool:
    name = (target.primary_document or "").lower()
    # a form whose exhibits are stored (an 8-K's EX-99.x, a 10-K's EX-13) reads its index for them
    return target.form in EXHIBITS or "/" in name or not name.endswith((".htm", ".html"))


def download(client: httpx.Client, url: str, max_bytes: int) -> Body:
    """Stream url into a compressed Body; Unusable if it's empty or over max_bytes uncompressed."""
    body = Compressing()
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(CHUNK):
            if body.size + len(chunk) > max_bytes:
                raise Unusable(f"over {max_bytes // 2**20} MB: {url}")
            body.update(chunk)
    if body.size == 0:
        raise Unusable(f"empty document: {url}")
    return body.finish()


def fetch_target(client: httpx.Client, target: Target, max_bytes: int) -> Fetched:
    """Raises httpx.HTTPError (SEC or network) or Unusable."""
    requests = 0
    exhibits: list[Exhibit] = []
    if needs_index(target):
        resp = client.get(index_url(target.cik, target.accession_no))
        requests += 1
        resp.raise_for_status()
        try:
            index = parse_index(resp.content, target.form)
        except ValueError as e:
            raise Unusable(str(e)) from e
        exhibits = parse_exhibits(resp.content, target.form)
    else:
        assert target.primary_document is not None
        index = FilingIndex(
            filed=target.filing_date,
            period_of_report=target.report_date,
            document_name=target.primary_document,
            url=archive_url(target.cik, target.accession_no, target.primary_document),
        )
    document = download(client, index.url, max_bytes)
    requests += 1
    stored = []
    for exhibit in exhibits:
        try:
            stored.append((exhibit, download(client, exhibit.url, max_bytes)))
        except Unusable:
            pass  # an empty or huge exhibit doesn't block the filing
        requests += 1
    return Fetched(index, document, stored, requests)
