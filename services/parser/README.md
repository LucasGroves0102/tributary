# parser

FR3, week 1. Consumes `documents.fetched` (from [doc-fetcher](../doc-fetcher)), reads the
document from `bronze.filing_documents` and its 8-K exhibits from `bronze.filing_exhibits`,
splits it into Part/Item sections and writes `silver.filing_sections` (`parser = python`,
`parser_version` = `PARSER_VERSION`). The [Query API](../query-api) reads sections with
citations from the `gold.filing_sections` view.

| | |
|---|---|
| Input | `documents.fetched` (consumer group `parser-python`), `bronze.filing_documents`, `bronze.filing_exhibits` |
| Output | `silver.filing_sections`: key, part, item, title, text, character offsets into the document text |
| Metrics | `tributary_documents_parsed_total{form}`, `tributary_parse_failures_total{form}`, `tributary_parse_duration_seconds{form}` |

Section keys: 10-K `1A`, 10-Q `II-1A`, 8-K `2.02`, 8-K exhibits `EX-99.1`. Re-parsing an
accession replaces its rows from either parser, so a document always has one parser's sections
([parser-rs](../parser-rs) is the Rust build of this service).

The algorithm is specified in the docstring of `src/filing_parser/sections.py` so the Rust
rewrite ([parser-rs](../parser-rs), week 4) can produce identical output for benchmarking. It
handles tables of contents, running page headers, 10-Qs without a "Part I" heading, MD&A
incorporated by reference (JPMorgan, Exxon: rule 7), SGML exhibit envelopes, and 10-K/10-Qs
that title their items without numbering them (rule 9: Molina, Edison International, TriNet
write "RISK FACTORS", never "Item 1A"). Rule 9 only runs when a 10-K or 10-Q has no "Item N"
heading at all, so every filing with numbered items parses exactly as before; it matches whole
lines against the official Form 10-K / 10-Q item titles (`TITLES_10K`, `TITLES_10Q`), takes the
item and Part from the title, and skips a title outside its Part ("Legal Proceedings" inside
Part I's financial statements). About 150 of 94,000 stored 10-K/10-Q documents had no sections
before it.

## Run

```bash
make up-all     # consumer, in docker-compose
make reparse    # parser --reparse-all: re-parse every stored document without Kafka, then exit
```

Use `make reparse` after changing the parsing rules.

### Re-parsing only some documents (production)

`--reparse-all` reads every stored document. When a rule change only affects documents that
currently have no sections (rule 9), re-queue just those through the document crawl's outbox
instead: `doc-crawler` publishes `documents.backfill` for every `done` target whose
`published_at` is NULL, 500 per round (every 15 minutes even when idle), and `parser-backfill`
re-parses them. After the new parser image is deployed (both `parser` and `parser-backfill`):

```sql
-- 1. what will be re-parsed: 10-K/10-Q documents without sections (incl. ones that errored)
SELECT d.form, count(*), count(t.accession_no) AS in_crawl_queue
FROM bronze.filing_documents d
LEFT JOIN ops.doc_crawl_targets t ON t.accession_no = d.accession_no AND t.status = 'done'
WHERE d.form ~ '^10-[KQ]'
  AND NOT EXISTS (SELECT 1 FROM silver.filing_sections s WHERE s.accession_no = d.accession_no)
GROUP BY d.form;

-- 2. republish them (the crawler sends them on its next round)
UPDATE ops.doc_crawl_targets t SET published_at = NULL
FROM bronze.filing_documents d
WHERE d.accession_no = t.accession_no AND t.status = 'done' AND d.form ~ '^10-[KQ]'
  AND NOT EXISTS (SELECT 1 FROM silver.filing_sections s WHERE s.accession_no = d.accession_no);

-- 3. progress: unpublished goes to 0, then the parser-backfill lag drains
SELECT unpublished FROM ops.doc_crawl_status;
SELECT parser_version, count(DISTINCT accession_no) FROM silver.filing_sections
WHERE parser_version IN ('python-4', 'rust-4') GROUP BY 1;
```

Re-running step 1 afterwards lists the documents rule 9 could not split (no Item headings and no
standard titles). Documents outside the crawl queue (`in_crawl_queue` lower than `count`:
stored by the live doc-fetcher outside the crawl's plan) are only reached by `--reparse-all`.
The hourly `section-index` job picks the new sections up by itself (`ops.section_index_pending`
sees re-parsed filings). Nothing here calls the SEC.

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`),
`METRICS_PORT`, `LOG_LEVEL`, the Kafka authentication variables of [libs/kafka](../../libs/kafka)
(SASL/SCRAM on k3s), `PARSER_TOPIC` / `PARSER_GROUP` (default `documents.fetched` /
`parser-python`). The chart's `parser-backfill` sets them to `documents.backfill` /
`parser-backfill` for the [document crawl](../doc-fetcher/README.md#document-crawl), so crawled
documents are parsed by a separate consumer and never queue ahead of live filings.

## Section diffs and search index (`parser-index`)

A batch job in the same image (`filing_parser/index.py`, migration 021) that turns parsed
sections into:

- **Search passages** (`gold.section_search`): every served section cut at line boundaries into
  ~2,000-character passages (`gold.refresh_section_search`), each with its `tsvector` and offsets
  but no text. The [Query API](../query-api/README.md#search)'s `/v1/search` ranks them and cuts
  snippets from `silver.filing_sections`.
- **What changed** (`gold.section_diffs`, `gold.section_changes`): Risk Factors and MD&A (10-K
  `1A`, `7`; 10-Q `II-1A`, `I-2`) compared with the previous 10-K / 10-Q and, for a 10-Q, the
  same quarter a year earlier. Paragraph-level, focused on substance: new, removed and rewritten
  paragraphs (with word-diff opcodes) are stored; unchanged ones, minor edits and number-only
  updates are counted. The algorithm and thresholds are specified in `filing_parser/diff.py`'s
  docstring (bump `ALGORITHM` with any change: every diff is recomputed). Diffs are Python, not
  SQL like the other transforms, because alignment needs fuzzy matching and difflib.

Incremental: `ops.section_index` is the checkpoint (one row per filing: when its sections were
parsed, which filings its diffs used). `ops.section_index_pending` returns filings whose sections
are new or re-parsed, and filings whose comparable filing changed (it arrived later, since the
crawl goes newest first, or was re-indexed) or whose diffs come from another algorithm version.
Each batch of filings commits with its passages, diffs and checkpoint rows, so a stopped run
resumes and a re-run is a no-op. One run at a time (advisory lock).

```bash
make section-index LIMIT=5000   # parser-index --limit 5000 [--batch 25]
```

Config: `DATABASE_URL`, `SECTION_INDEX_LIMIT` (default 5000), `SECTION_INDEX_BATCH` (25). Helm:
CronJob `section-index` (hourly; ships suspended, running on hermes since the crawl finished). Progress: `ops.section_index_status`;
counts per compared section: `ops.section_change_summary`.

Cost on a sample of 78 real filings (10 companies, 12.8 MB of section text): 3 s of
`to_tsvector` for passages, ~35 ms per section diff. Search table + indexes: ~1.3x the raw
section text when the GIN index is freshly built (tsvectors ~1x, GIN 0.25x, b-trees 0.04x); the
GIN index grows to ~0.5-0.7x under incremental inserts, so REINDEX it after a bulk load.

## Tests

`pytest services/parser`: `test_sections.py` parses real NVIDIA 10-K, 10-Q and 8-K documents
(`tests/fixtures/*.htm.gz`), three real Q3 2026 10-Qs with titled but unnumbered items for rule 9
(`moh-`, `eix-`, `tnet-10-Q.htm.gz`: Molina, Edison International, TriNet; the hidden inline-XBRL
header is emptied to save space, the extracted text is unchanged), plus small synthetic cases for
the quirks above; `test_db.py` needs
`TEST_DATABASE_URL`. `test_diff.py` runs the diff rules on real NVIDIA sections (fiscal 2026 vs
2025 10-K; Q2 FY2027 10-Q vs Q1 and vs Q2 FY2026); `test_index_db.py` runs `parser-index` on
them in Postgres (incremental runs, late priors, re-parses, algorithm bumps).
`test_rust_service.py` runs both parser services on the same stored documents and expects
identical rows (`PARSER_RS_BIN`, `TEST_DATABASE_URL`); `test_rust_tls.py` checks that the Rust
parser connects or fails for every `sslmode` exactly where libpq does, against a TLS-only server
(CI).
