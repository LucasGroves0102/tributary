-- SQL workspace (B3a): notebooks as saved work, and each user's daily query seconds.
--
-- A notebook is a workspace item of kind `notebook`: ordered cells of SQL, text (Markdown) and
-- charts over a SQL cell's result (query_api/workspaces.py NotebookState). It holds the user's
-- text only, never a result: a cell is run again, through POST /v1/sql as the viewer, every time
-- it is opened. Up to 65,536 bytes like a model (the Query API caps it at 60,000 compact bytes).
-- Owner-only like every other item: 004's forced row-level security applies unchanged.
--
-- sql_usage: per user per UTC day, the seconds their queries ran and how many (the Query API's
-- daily budget, SQL_SECONDS_PER_DAY, counted across replicas by one upsert per query). Owner-only
-- like api_key_usage (019): FORCEd row-level security on app.user_id, the Query API filters on
-- the user too; not streamed (it grants nothing), REPLICA IDENTITY default. Rows go with the user
-- (ON DELETE CASCADE: demo-cleanup).
--
-- Lock impact (hermes): DROP and ADD CONSTRAINT take an ACCESS EXCLUSIVE lock on workspace_items
-- for the transaction and the new CHECKs read every row once (a few hundred rows: milliseconds);
-- saves and reads of saved work wait for that instant. They only widen what is allowed. The new
-- table's foreign key takes a SHARE ROW EXCLUSIVE lock on `users` for the instant. 5 s at most,
-- then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('chart', 'comps', 'screen', 'report', 'thesis', 'model', 'metric',
                    'notebook'));

ALTER TABLE workspace_items DROP CONSTRAINT workspace_items_state_check;
ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_state_check
    CHECK (jsonb_typeof(state) = 'object'
           AND octet_length(state::text)
               <= CASE WHEN kind IN ('report', 'thesis') THEN 131072
                       WHEN kind IN ('model', 'notebook') THEN 65536
                       ELSE 16384 END);

CREATE TABLE sql_usage (
    user_id text          NOT NULL REFERENCES users ON DELETE CASCADE,
    day     date          NOT NULL,
    seconds numeric(12,3) NOT NULL DEFAULT 0 CHECK (seconds >= 0),
    queries integer       NOT NULL DEFAULT 0 CHECK (queries >= 0),
    PRIMARY KEY (user_id, day)
);

ALTER TABLE sql_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE sql_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON sql_usage
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
