-- Storage diet: document bodies move out of Postgres into zstd files (the body store, libs/bodies;
-- db/DESIGN.md "150"). The owner's go-ahead (2026-11-17): bodies as compressed files "as long as it
-- doesn't affect performance". Raw companyfacts JSON (bronze.company_facts) stays.
--
-- A body is one file named by the SHA-256 of its uncompressed bytes (the row's content_sha256):
-- <BODIES_DIR>/<sha[:2]>/<sha>.zst, zstd level 12 with a frame checksum. Postgres keeps the metadata.
--
-- 1. bronze.filing_documents and bronze.filing_exhibits: content_gzip becomes nullable and body_bytes
--    (the file's size) is added. A row has its body in exactly one place: content_gzip (stored before
--    this migration, not moved yet) or the file (body_bytes). The CHECK is NOT VALID: every existing
--    row has content_gzip (it was NOT NULL), so VALIDATE would only scan; new rows and updates are
--    checked. Writers (doc-fetcher, the crawl) write the file first, then the row, document +
--    exhibits + outbox in one transaction as before (059): "stored whole or not at all" holds.
-- 2. Partial indexes for the move, oldest filing first: the rows still in content_gzip. New rows
--    never enter them; empty once the move is done.
-- 3. The catch-up kind 'bodies' (071's machinery; services/parser bodies.py, run by the hourly
--    section-index job): moves a budget of bodies per run, oldest filing first, a document with its
--    exhibits in one transaction: gunzip, check the SHA-256 and size, write the zstd file, read it
--    back from disk and check it again, then NULL the column. Ships running; pause with
--      UPDATE ops.catchup SET paused = true WHERE kind = 'bodies';
--    A daily sweep in the same step moves files no row references (older than a day) to trash/,
--    deletes the trash after a week, and counts rows whose file is missing (catchup_status.failed).
--    ops.company_builds is per company: no 'bodies' value there (nothing stamps per company).
--
-- Measured on hermes (2026-11-17, read-only; the sample 635 documents and 346 exhibits by
-- TABLESAMPLE BERNOULLI, compressed on a laptop with python-zstandard 0.25 / libzstd 1.5.7):
--   filing_documents 650,435 bodies, 29.35 GB gzip (456 GB raw, avg 45 KB gzip), TOAST 29 GiB;
--   filing_exhibits 365,471 bodies, 4.93 GB gzip (avg 13 KB), TOAST 4.9 GiB. Full dump 47 GB
--   (2026-11-15), DB 179 GB, 118 GB free.
--   zstd-12 / gzip-9 bytes: documents 0.720, exhibits 0.773; with 4 KiB block rounding 0.771 and
--   0.922 (exhibits are small): files ~27 GB for the ~36 GB of TOAST, and the weekly full dump
--   loses the ~34 GB of bodies it carries. zstd-9 0.741 / 0.794; zstd-19 0.680 / 0.751 at 7 MB/s.
--   zstd-12 compresses ~150 MB/s raw (the move: ~1.5 h of one core for everything) and decompresses
--   ~4 GB/s, gzip 1.2 GB/s.
--   Reading 199 random documents' bodies today (EXPLAIN ANALYZE, the owner login): 138 ms cold,
--   3.4 ms warm (9.7 MB gzip); 178 exhibits 94 ms cold, 1.8 ms warm. A heap scan for
--   content_gzip IS NOT NULL: 42 ms (documents), 147 ms (exhibits): each index builds in under a
--   second. No two rows' hashes differ in format (all 64 lowercase hex); 119 document and 232
--   exhibit hashes repeat (one file serves them all).
--
-- Locks on hermes (lock_timeout 3 s): CREATE INDEX on both tables (SHARE: inserts wait, reads go on;
-- under a second each), then ALTER TABLE ... DROP NOT NULL / ADD COLUMN (nullable, no default) /
-- ADD CONSTRAINT ... NOT VALID (catalog only) last, so the ACCESS EXCLUSIVE lock is held only to the
-- commit; the CHECK constraint on ops.catchup replaced (9 rows, read by the hourly jobs). No table is rewritten. Space comes
-- back to the filesystem only with VACUUM FULL of the two tables once the move is done (owner
-- command and check query: services/parser README "Document bodies").

SET LOCAL lock_timeout = '3s';

-- 2. The move's backlog -----------------------------------------------------------------------------

CREATE INDEX filing_documents_in_column ON bronze.filing_documents (filed, accession_no)
    WHERE content_gzip IS NOT NULL;
CREATE INDEX filing_exhibits_in_column ON bronze.filing_exhibits (accession_no, exhibit_type)
    WHERE content_gzip IS NOT NULL;

-- 3. The catch-up kind ------------------------------------------------------------------------------

ALTER TABLE ops.catchup DROP CONSTRAINT catchup_kind_check;
ALTER TABLE ops.catchup ADD CONSTRAINT catchup_kind_check
    CHECK (kind IN ('parser', 'passages', 'diffs', 'gold', 'check_financials', 'check_segments',
                    'check_sections', 'statements', 'known', 'bodies'));
INSERT INTO ops.catchup (kind, paused, baseline) VALUES ('bodies', false, NULL);

-- 1. Where a body is ---------------------------------------------------------------------------------

ALTER TABLE bronze.filing_documents
    ALTER COLUMN content_gzip DROP NOT NULL,
    ADD COLUMN body_bytes integer,
    ADD CONSTRAINT filing_documents_body CHECK (content_gzip IS NOT NULL OR body_bytes IS NOT NULL)
        NOT VALID;
ALTER TABLE bronze.filing_exhibits
    ALTER COLUMN content_gzip DROP NOT NULL,
    ADD COLUMN body_bytes integer,
    ADD CONSTRAINT filing_exhibits_body CHECK (content_gzip IS NOT NULL OR body_bytes IS NOT NULL)
        NOT VALID;

COMMENT ON COLUMN bronze.filing_documents.content_gzip IS
    'gzip of the document as fetched; NULL once the body is a file in the body store (migration 150)';
COMMENT ON COLUMN bronze.filing_documents.body_bytes IS
    'size of the body''s zstd file, <BODIES_DIR>/<content_sha256[:2]>/<content_sha256>.zst (150)';
COMMENT ON COLUMN bronze.filing_exhibits.content_gzip IS
    'gzip of the exhibit as fetched; NULL once the body is a file in the body store (migration 150)';
COMMENT ON COLUMN bronze.filing_exhibits.body_bytes IS
    'size of the body''s zstd file, <BODIES_DIR>/<content_sha256[:2]>/<content_sha256>.zst (150)';
