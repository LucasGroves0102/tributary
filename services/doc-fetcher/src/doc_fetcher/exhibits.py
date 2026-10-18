"""Fetch EX-99.x exhibits for stored 8-Ks that don't have them yet (make exhibits)."""

import logging
import os

import httpx
import psycopg

from doc_fetcher.db import eight_ks_without_exhibits
from doc_fetcher.index import index_url
from doc_fetcher.main import fetch_exhibits
from tributary_sec import sec_client

log = logging.getLogger("doc_fetcher.exhibits")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, \
            sec_client(os.environ["SEC_USER_AGENT"]) as client:
        pending = eight_ks_without_exhibits(conn)
        log.info("%d 8-Ks without exhibits", len(pending))
        for accession, cik in pending:
            try:
                resp = client.get(index_url(cik, accession))
                resp.raise_for_status()
                n = fetch_exhibits(conn, client, accession, resp.content)
                log.info("%s: %d exhibits", accession, n)
            except httpx.HTTPError as e:
                log.error("%s: %s", accession, e)


if __name__ == "__main__":
    main()
