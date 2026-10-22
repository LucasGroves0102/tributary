-- Customizable views (Phase 6): per-user layout preferences, one row per view: Home's card
-- arrangement ('home': which cards show, in what order) and the column sets of the screener
-- ('screener') and the watchlist ('watchlist'). No row = the default; deleting it resets.
--
-- Owner-only, like 004's saved work: the Query API filters on the JWT `sub`, and the forced
-- row-level security admits only rows whose user_id equals the transaction's `app.user_id`.
-- A preference grants nothing (column keys and card ids, validated against fixed catalogs in
-- query_api/views.py); the pages still read every value through the permission-checked
-- routes. Not streamed by Debezium. Goes with the user (ON DELETE CASCADE).

CREATE TABLE user_views (
    user_id    text        NOT NULL REFERENCES users ON DELETE CASCADE,
    view       text        NOT NULL CHECK (view IN ('home', 'screener', 'watchlist')),
    state      jsonb       NOT NULL CHECK (jsonb_typeof(state) = 'object'
                                           AND octet_length(state::text) <= 4096),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, view)
);

ALTER TABLE user_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_views FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON user_views
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
