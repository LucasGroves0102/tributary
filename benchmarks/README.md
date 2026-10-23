# benchmarks

## Rust vs Python parser

The document parser ([services/parser-rs](../services/parser-rs)) is a Rust rewrite of the
Python reference ([services/parser](../services/parser)), built against the algorithm written
in `filing_parser/sections.py`'s docstring. Both write `silver.filing_sections` tagged with
`parser = rust | python`, so they can run side by side and be compared row for row.

**Correctness.** Before the swap, both parsers ran over the same 186 stored 10-K, 10-Q and 8-K
documents (with exhibits): every section key, title, offset and text hash matched.

**In production.** Rust has been the live parser since 2026-10-20 (`image: parser-rs` in
[deploy/helm/tributary/values.yaml](../deploy/helm/tributary/values.yaml); the Python image
stays one value away for rollback). It parsed the whole 5-year document crawl as it arrived:
265,467 filings into 1.78M sections over 27 hours (~10,000 filings an hour), with the crawl, at
the SEC's 10 requests/s, not the parser, setting the pace.

Check on the server which parser wrote recent sections:

```sql
SELECT parser, count(*) FROM silver.filing_sections
WHERE parsed_at > now() - interval '24 hours' GROUP BY 1;
```
