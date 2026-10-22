# Kafka topics

| Topic | Key | Producer | Consumers | Value |
|---|---|---|---|---|
| `filings.raw` | CIK | poller, poller-backfill, doc-fetcher-seed | bronze-loader, doc-fetcher | `{accession_no, cik, company_name, role, form_type, filed_at, url, source}` |
| `documents.fetched` | CIK | doc-fetcher | parser: Python or parser-rs, group `parser-python` | `{accession_no, cik, form, document_name, url, content_sha256, size_bytes}` |
| `documents.backfill` | CIK | doc-crawler (document crawl) | parser-backfill: same image as parser, group `parser-backfill` | `documents.fetched`'s shape plus `source: "crawl"` |

`filings.raw` has one event per filer, so a Form 4 produces two events (Reporting + Issuer)
with the same accession number. Consumers must be idempotent on `(accession_no, cik)`.

`source` is `feed` (live poller; `filed_at` is the exact acceptance time), `daily-index`
(backfill; `filed_at` is midnight US Eastern and `role` is `Unknown`) or `seed`
(doc-fetcher-seed history load; `filed_at` is the exact acceptance time, corrected from the
submissions API, and `role` is `Filer`). Only `feed` events count in freshness and live-coverage
metrics. Events from before the field existed have no `source` and came from the feed; earlier
seed events without it were marked `seed` in bronze by `db/migrations/016_seed_source.sql`.

`documents.fetched` references the document in `bronze.filing_documents` instead of
embedding it: a 10-K is ~2 MB of HTML, over Kafka's default 1 MB message limit.

`documents.backfill` carries the same reference for documents stored by the document crawl
(hundreds of thousands of filings over days). It is a separate topic with its own consumer group,
so the live parser never has crawled documents queued ahead of a new filing, its
`KafkaConsumerLagGrowing` alert keeps meaning "live parsing is behind", and a document that stops
a parser stops only the backfill one. Both parsers write the same rows and are idempotent per
accession, so an event published twice (the crawl republishes unconfirmed ones) is harmless.

Each producer and consumer above is a Kafka principal with ACLs for exactly these topics and its
group once Kafka SASL is on (`deploy/helm/platform/files/kafka-acls.yaml`: `poller`,
`poller-backfill`, `doc-fetcher-seed`, `bronze-loader`, `doc-fetcher`, `doc-crawler`, `parser`,
`parser-backfill`). A new producer or consumer of a topic needs its ACL there in the same change.

## CDC topics (Debezium, from the `app` database)

| Topic | Key | Producer | Consumers |
|---|---|---|---|
| `app.public.users` | `{user_id}` | Debezium | authz-sync |
| `app.public.subscriptions` | `{user_id}` | Debezium | authz-sync |
| `app.public.company_grants` | `{grant_id}` | Debezium | authz-sync |
| `app.public.plan_tiers` | `{plan_id, tier}` | Debezium | authz-sync |
| `app.public.debezium_heartbeat` | `{id}` | Debezium (its own heartbeat query, every 10 s) | none |
| `__debezium-heartbeat.app` | `{serverName}` | Debezium heartbeat | none |

Only `connect` may write these topics and only `authz-sync` may read the four access tables
(ACLs in `kafka-acls.yaml`), so no other client can forge or read a grant.

Values are Debezium envelopes `{before, after, op, source, ...}` (JSON, no schemas). Topics
are compacted, and each delete is followed by a tombstone. See `streaming/debezium/`.
The heartbeat topics only exist so Debezium's replication slot keeps advancing while the app
tables are idle; nothing consumes them.
