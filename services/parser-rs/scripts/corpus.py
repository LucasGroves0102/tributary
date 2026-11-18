"""Write the parser corpus: the repo's filing fixtures plus documents and exhibits from bronze.

Usage: DATABASE_URL=... [BODIES_DIR=...] python scripts/corpus.py OUT_DIR [--limit 192]

Documents are written gzipped (a body moved to the body store, migration 150, is read from its file
under BODIES_DIR and gzipped again), one file each, and listed in OUT_DIR/manifest.jsonl
in the input format of `parser-rs` and scripts/bench.py. Reads Postgres only; rows are streamed.
"""

import argparse
import gzip
import json
import os
import re
from pathlib import Path

import psycopg

from tributary_bodies import Store, read_body

REPO = Path(__file__).resolve().parents[3]
FIXTURES = [REPO / "services/parser/tests/fixtures"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--limit", type=int, default=192, help="primary documents from bronze")
    args = ap.parse_args()
    docs = args.out / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    with (args.out / "manifest.jsonl").open("w") as manifest:
        def add(entry: dict, path: Path) -> None:
            entry["path"] = str(path.resolve())
            entry["bytes"] = len(gzip.decompress(path.read_bytes()))
            manifest.write(json.dumps(entry) + "\n")

        for d in FIXTURES:
            for f in sorted(d.glob("*.htm.gz")):
                form = re.fullmatch(r"\w+-(.+)\.htm\.gz", f.name).group(1)  # nvda-10-K.htm.gz
                add({"id": f"fixture/{f.name}", "source": "fixture", "form": form}, f)

        store = Store.from_env()

        def gz(content: bytes | None, sha256: str) -> bytes:
            return content if content is not None else gzip.compress(
                read_body(store, None, sha256))

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            conn.read_only = True
            accessions = []
            with conn.cursor(name="documents") as cur:
                cur.execute("SELECT accession_no, form, content_gzip, content_sha256"
                            " FROM bronze.filing_documents"
                            " ORDER BY filed, accession_no LIMIT %s", (args.limit,))
                for accession_no, form, content, sha256 in cur:
                    path = docs / f"{accession_no}.htm.gz"
                    path.write_bytes(gz(content, sha256))
                    accessions.append(accession_no)
                    add({"id": accession_no, "source": "bronze", "form": form}, path)
            with conn.cursor(name="exhibits") as cur:
                cur.execute("SELECT accession_no, exhibit_type, description, content_gzip,"
                            " content_sha256 FROM bronze.filing_exhibits"
                            " WHERE accession_no = ANY(%s)"
                            " ORDER BY accession_no, exhibit_type", (accessions,))
                for accession_no, exhibit_type, description, content, sha256 in cur:
                    path = docs / f"{accession_no}.{exhibit_type}.htm.gz"
                    path.write_bytes(gz(content, sha256))
                    add({"id": f"{accession_no}/{exhibit_type}", "source": "bronze",
                         "exhibit_type": exhibit_type, "description": description}, path)


if __name__ == "__main__":
    main()
