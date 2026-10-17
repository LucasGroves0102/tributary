# poller

FR1, week 1. Polls the EDGAR latest-filings feed and publishes each new filing to the
`filings.raw` Kafka topic, keyed by CIK.

Feed: https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom

Each feed entry is one *filer* of a filing, so a Form 4 appears twice (insider = Reporting,
company = Issuer) with the same accession number. Events are deduped on `(accession_no, cik)`
and carry `role` so downstream stages can filter.

Config (env): `SEC_USER_AGENT` (required), `KAFKA_BOOTSTRAP_SERVERS`, `POLL_INTERVAL_SECONDS`
(default 30), `LOG_LEVEL`.
