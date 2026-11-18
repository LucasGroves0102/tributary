# tributary-bodies

The body store (migration 150): every stored document and exhibit body as one zstd file, named by
the SHA-256 of its uncompressed bytes (the row's `content_sha256`), under `BODIES_DIR`:

    <BODIES_DIR>/<sha256[:2]>/<sha256>.zst

zstd level 12 with a frame checksum: 0.72 of the gzip-9 bytes Postgres held on sampled hermes
documents, 0.77 on exhibits, decompressed about three times faster (db/DESIGN.md "150"). Postgres
keeps the metadata: the hash, `size_bytes` (raw) and `body_bytes` (the file); `content_gzip` is NULL
once a body is a file. The Rust parser reads the same paths (services/parser-rs `db.rs`).

```python
from tributary_bodies import Body, Store, read_body

store = Store.from_env()                  # BODIES_DIR, or None
body = Body.of(html)                      # compress + hash; Compressing() for a stream
body_bytes = store.put(body)              # BEFORE the transaction that inserts the row
html = read_body(store, content_gzip, sha256)   # the column while it has one, else the file
```

- **Writing** (`Store.put`): a temporary name, fsync, rename into place, the directory fsynced; a
  body already there is only touched (the sweep then leaves it). Idempotent by hash, so identical
  bodies share one file. A file whose row never commits is an orphan: harmless.
- **Reading**: `read_body` / `open_body` (a stream, for large documents) take a row's
  `content_gzip` or its file; a missing file raises `MissingBody`, a `KeyError` (the parsers skip
  it, as a missing row).
- **The sweep's tools** (services/parser `bodies.py` runs it daily): `files()` lists bodies and
  their mtimes, `trash()` moves an orphan to `trash/` unless it was touched since, `untrash()` puts
  one back, `purge_trash()` empties it after a week, `remove_temporaries()` clears a crashed
  writer's leftovers.

Writers: doc-fetcher (live, the crawl, `doc-fetcher-exhibits`, `doc-fetcher-bodies`) and the
`bodies` catch-up. Readers: both parsers and facts-fetcher's segments job. Tests:
`.venv/bin/pytest libs/bodies`.
