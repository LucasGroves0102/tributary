-- FR7/FR8: who can read what, derived from the app database by authz-sync (via Debezium).
-- The Query API's row-level security (second layer, step 7) reads this table; OpenFGA is the
-- first layer. Revoking a grant in the app database must remove rows here within 30 s.
CREATE SCHEMA authz;

CREATE TABLE authz.user_access (
    user_id    text        NOT NULL,
    company_id text        NOT NULL,
    tier       text        NOT NULL CHECK (tier IN ('financials', 'filings')),
    synced_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id, tier)
);
