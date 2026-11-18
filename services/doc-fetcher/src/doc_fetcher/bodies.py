"""doc-fetcher-bodies: find, and with --refetch re-fetch, document bodies missing from the body
store (migration 150).

Body files are not backed up (deploy/host README "Document bodies"): they are the SEC's, at the URL
each row keeps. This reads every row whose body is a file (content_gzip NULL), checks the file is
there and, with --refetch, downloads each missing one through libs/sec (the shared rate limit;
SEC_CALLER_CLASS, bulk in Helm's `bodies-refetch`) and stores it under its hash, only when the
download's SHA-256 is the row's: a file the SEC changed since is reported, never stored. Rows are
untouched (they already name the hash). The hourly sweep (services/parser bodies.py) counts the
missing in ops.catchup_status (`failed`, kind bodies).

    doc-fetcher-bodies                       # count the missing, no SEC requests
    doc-fetcher-bodies --refetch [--limit N]
"""

import argparse
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx
import psycopg

from doc_fetcher.crawl_fetch import Unusable, download
from doc_fetcher.db import body_store
from tributary_bodies import Body, Store
from tributary_sec import sec_client
from tributary_telemetry import configure_logging

log = logging.getLogger("doc_fetcher.bodies")

MAX_BYTES = 200 * 2**20  # larger than any stored body (84 MB on hermes, 2026-11-17)


@dataclass(frozen=True)
class Missing:
    accession_no: str
    exhibit_type: str | None
    url: str
    sha256: str
    size: int


@dataclass
class Result:
    checked: int = 0
    missing: int = 0
    fetched: int = 0
    changed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


PAGE = 5000


def missing(conn: psycopg.Connection, store: Store, result: Result) -> Iterator[Missing]:
    """Rows whose body file isn't in the store, one per hash (identical bodies share a file), read
    a page at a time (short queries: a re-fetch at the SEC's rate can take a day)."""
    seen: set[str] = set()
    for sql in (
        "SELECT accession_no, '', url, content_sha256, size_bytes FROM bronze.filing_documents"
        " WHERE content_gzip IS NULL AND accession_no > %(a)s ORDER BY accession_no LIMIT %(n)s",
        "SELECT accession_no, exhibit_type, url, content_sha256, size_bytes"
        " FROM bronze.filing_exhibits"
        " WHERE content_gzip IS NULL AND (accession_no, exhibit_type) > (%(a)s, %(t)s)"
        " ORDER BY accession_no, exhibit_type LIMIT %(n)s",
    ):
        after = {"a": "", "t": "", "n": PAGE}
        while rows := conn.execute(sql, after).fetchall():
            after.update(a=rows[-1][0], t=rows[-1][1])
            for accession_no, exhibit_type, url, sha256, size in rows:
                result.checked += 1
                if sha256 in seen or store.exists(sha256):
                    continue
                seen.add(sha256)
                result.missing += 1
                yield Missing(accession_no, exhibit_type or None, url, sha256, size)


def refetch(store: Store, m: Missing, fetch: Callable[[str], Body]) -> bool:
    """Download the body and store it if it is still the row's; False if the SEC's copy changed."""
    body = fetch(m.url)
    if body.sha256 != m.sha256 or body.size != m.size:
        return False
    store.put(body)
    return True


def run(conn: psycopg.Connection, store: Store, fetch: Callable[[str], Body] | None,
        limit: int | None = None) -> Result:
    """Count the missing bodies; with `fetch`, re-fetch up to `limit` of them."""
    result = Result()
    for m in missing(conn, store, result):
        label = m.accession_no + (f" {m.exhibit_type}" if m.exhibit_type else "")
        if fetch is None or (limit is not None and result.fetched + len(result.changed)
                             + len(result.errors) >= limit):
            continue
        try:
            if refetch(store, m, fetch):
                result.fetched += 1
            else:
                result.changed.append(label)
                log.error("%s: the SEC's copy is not the stored body (%s); not stored", label,
                          m.url)
        except (httpx.HTTPError, Unusable) as e:
            result.errors.append(label)
            log.error("%s: %s", label, e)
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refetch", action="store_true", help="download the missing bodies")
    ap.add_argument("--limit", type=int, help="at most this many downloads")
    args = ap.parse_args(argv)
    configure_logging()
    store = body_store()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if not args.refetch:
            result = run(conn, store, None)
        else:
            with sec_client(os.environ["SEC_USER_AGENT"]) as client:
                result = run(conn, store, lambda url: download(client, url, MAX_BYTES),
                             args.limit)
    log.info("%d bodies checked, %d missing, %d re-fetched, %d changed at the SEC, %d errors",
             result.checked, result.missing, result.fetched, len(result.changed),
             len(result.errors))


if __name__ == "__main__":
    main()
