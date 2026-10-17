-- Bronze: events exactly as received, plus the columns needed to dedupe and index them.

CREATE SCHEMA bronze;

-- One row per (filing, filer), from the filings.raw topic.
CREATE TABLE bronze.filings (
    accession_no    text        NOT NULL,
    cik             text        NOT NULL,
    form_type       text        NOT NULL,
    filed_at        timestamptz NOT NULL,
    payload         jsonb       NOT NULL,  -- the Kafka message value, untouched
    kafka_partition int         NOT NULL,
    kafka_offset    bigint      NOT NULL,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, cik)
);

CREATE INDEX filings_cik_filed_at ON bronze.filings (cik, filed_at DESC);
CREATE INDEX filings_form_type_filed_at ON bronze.filings (form_type, filed_at DESC);
