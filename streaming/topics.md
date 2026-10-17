# Kafka topics

| Topic | Key | Producer | Consumers | Value |
|---|---|---|---|---|
| `filings.raw` | CIK | poller, doc-fetcher-seed | bronze-loader, doc-fetcher | `{accession_no, cik, company_name, role, form_type, filed_at, url}` |
| `documents.fetched` | CIK | doc-fetcher | parser (week 4: parser-rs) | `{accession_no, cik, form, document_name, url, content_sha256, size_bytes}` |

`filings.raw` has one event per filer, so a Form 4 produces two events (Reporting + Issuer)
with the same accession number. Consumers must be idempotent on `(accession_no, cik)`.

`documents.fetched` references the document in `bronze.filing_documents` instead of
embedding it: a 10-K is ~2 MB of HTML, over Kafka's default 1 MB message limit.
