-- Thesis checks (Phase 9 B2, "Check my thesis"): the history of checks the assistant ran on a
-- thesis (009). One row per check: when, a SHA-256 of the thesis text it checked (so the web
-- app can say a check is stale once the text changed), the model, its estimated cost, a few
-- counts for the history list, and the result (claims with citations, the models' assumptions
-- against history, points the thesis doesn't mention).
--
-- A table, not the thesis's state: the thesis state is the user's own text, written through
-- the generic item routes, so a verdict stored there could be written by the browser; only
-- agent-api writes checks (the Query API's POST /v1/thesis-checks/{id}, kept off the public site
-- by nginx, validating the result strictly). It also keeps a history of results (up to ~120 KB
-- each; the Query API keeps the newest 20 per thesis) out of the thesis's 128 KiB.
--
-- Owner-only like the rest of saved work: the Query API filters every statement on the JWT
-- `sub`, and the row-level security below is FORCEd so it binds the table owner too. A check
-- belongs to its thesis's owner ((item_id, user_id) references the item) and goes with it
-- (ON DELETE CASCADE; cascades bypass RLS, as in 004). Not streamed by Debezium (the
-- publication is filtered to the tables that grant access); a check grants nothing.

ALTER TABLE workspace_items ADD CONSTRAINT workspace_items_item_user UNIQUE (item_id, user_id);

CREATE TABLE thesis_checks (
    check_id   uuid             PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id    uuid             NOT NULL,
    user_id    text             NOT NULL,
    text_hash  text             NOT NULL CHECK (text_hash ~ '^[0-9a-f]{64}$'),
    model      text             NOT NULL CHECK (char_length(model) BETWEEN 1 AND 60),
    cost_usd   double precision CHECK (cost_usd >= 0),
    counts     jsonb            NOT NULL CHECK (jsonb_typeof(counts) = 'object'
                                                AND octet_length(counts::text) <= 1024),
    result     jsonb            NOT NULL CHECK (jsonb_typeof(result) = 'object'
                                                AND octet_length(result::text) <= 131072),
    created_at timestamptz      NOT NULL DEFAULT now(),
    FOREIGN KEY (item_id, user_id) REFERENCES workspace_items (item_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX thesis_checks_item ON thesis_checks (item_id, created_at DESC);

ALTER TABLE thesis_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE thesis_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON thesis_checks
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
