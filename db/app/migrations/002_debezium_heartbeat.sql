-- Debezium's heartbeat writes here every few seconds, so its replication slot keeps advancing
-- while the app tables are idle. A slot that doesn't advance pins WAL from every database on
-- the server, including bulk loads into the tributary database.

CREATE TABLE debezium_heartbeat (
    id integer     PRIMARY KEY,
    ts timestamptz NOT NULL
);

INSERT INTO debezium_heartbeat (id, ts) VALUES (1, now());
