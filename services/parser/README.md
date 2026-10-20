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
accession replaces its rows for this parser.

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
`METRICS_PORT`, `LOG_LEVEL`.

## Tests

`pytest services/parser`: `test_sections.py` parses real NVIDIA 10-K, 10-Q and 8-K documents
(`tests/fixtures/*.htm.gz`) plus small synthetic cases for the quirks above; `test_db.py` needs
`TEST_DATABASE_URL`.
