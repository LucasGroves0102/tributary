# parser-rs

FR3, week 4. Rust rewrite of the [parser](../parser), in progress. It implements the algorithm
specified in the docstring of `services/parser/src/filing_parser/sections.py`, is benchmarked
against the Python version on the same filings, and will be swapped in behind the same Kafka
topic (`documents.fetched`), writing `silver.filing_sections` with `parser = rust`.
