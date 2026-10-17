# Kafka topics

| Topic | Key | Producer | Value |
|---|---|---|---|
| `filings.raw` | CIK | poller | `{accession_no, cik, company_name, role, form_type, filed_at, url}` |

`filings.raw` has one event per filer, so a Form 4 produces two events (Reporting + Issuer)
with the same accession number. Consumers must be idempotent on `(accession_no, cik)`.
