# parser

FR3, week 1. Consumes `documents.fetched`, reads the document from
`bronze.filing_documents`, splits it into Part/Item sections and writes
`silver.filing_sections` (parser = `python`). Read sections with citations from
`gold.filing_sections`.

The algorithm is specified in the docstring of `src/filing_parser/sections.py` so the Rust rewrite
(`services/parser-rs`, week 4) can produce identical output for benchmarking.

`parser --reparse-all` re-parses every stored document without Kafka; use it after
changing the parsing rules.

Known limitation: filers that incorporate their annual report by reference (JPMorgan, Exxon)
keep MD&A under non-Item headings, so their Item 7 is a one-line pointer.

Config (env): `DATABASE_URL` (required), `KAFKA_BOOTSTRAP_SERVERS`, `LOG_LEVEL`.
