# streaming

Kafka topic contracts and CDC configuration.

- [`topics.md`](topics.md): every topic, its key, producers, consumers and event shape:
  `filings.raw`, `documents.fetched`, and the Debezium CDC topics `app.public.*`.
- [`debezium/`](debezium): the Kafka Connect connector that streams the app database's access
  tables to Kafka (`make cdc`).

Locally, `make up` starts Kafka (host port 9092; topics are auto-created on first produce),
Kafka Connect (8083) and kafka-ui (http://localhost:8080). `make topics` lists topics and
`make consume` tails `filings.raw`. On k3s, Kafka and Connect run in the `platform` namespace
([deploy/helm/platform](../deploy/helm/platform)).
