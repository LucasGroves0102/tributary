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
incorporated by reference (JPMorgan, Exxon: rule 7) and SGML exhibit envelopes.

## Run

```bash
make up-all     # consumer, in docker-compose
make reparse    # parser --reparse-all: re-parse every stored document without Kafka, then exit
```

Use `make reparse` after changing the parsing rules.

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
(`tests/fixtures/*.htm.gz`) plus small synthetic cases for the quirks above; `test_db.py` needs
`TEST_DATABASE_URL`. `test_diff.py` runs the diff rules on real NVIDIA sections (fiscal 2026 vs
2025 10-K; Q2 FY2027 10-Q vs Q1 and vs Q2 FY2026); `test_index_db.py` runs `parser-index` on
them in Postgres (incremental runs, late priors, re-parses, algorithm bumps).
`test_rust_service.py` runs both parser services on the same stored documents and expects
identical rows (`PARSER_RS_BIN`, `TEST_DATABASE_URL`); `test_rust_tls.py` checks that the Rust
parser connects or fails for every `sslmode` exactly where libpq does, against a TLS-only server
(CI).
