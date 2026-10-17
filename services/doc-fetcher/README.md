# doc-fetcher

FR2b, week 1. Consumes `filings.raw`; for 10-K, 10-Q and 8-K filings by tracked companies,
fetches the filing index page, then the primary document, stores it gzip-compressed in
`bronze.filing_documents` and publishes `documents.fetched`. Each accession is fetched once.

`doc-fetcher-seed` loads recent history: it reads each tracked company's submissions and
publishes its last 2 10-Ks and last 4 10-Qs (within 2 years) and last 180 days of 8-Ks
to `filings.raw`, so
history flows through the same path as live filings.

Documents are referenced, not embedded, in `documents.fetched`: a 10-K is ~2 MB of HTML,
over Kafka's default 1 MB message limit.

Config (env): `SEC_USER_AGENT`, `DATABASE_URL` (both required), `KAFKA_BOOTSTRAP_SERVERS`,
`LOG_LEVEL`.
