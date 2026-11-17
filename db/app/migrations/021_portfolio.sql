-- Portfolio and decision journal (ROADMAP 3, investor features): what a user owns, linked to the
-- research behind it, and a journal of why they bought, added, trimmed, sold or kept it. The Query
-- API's /v1/portfolio routes (query_api/portfolio.py) are the only reader and writer.
--
-- portfolio_holdings: one per company (company_id) the user holds, or per symbol for a position
--   Tributary doesn't cover (an ETF or a fund: company_id NULL, `symbol` and the broker's `name`).
--   Optionally linked to the user's own Analysis project or thesis (ON DELETE SET NULL of that
--   column only). `price`, `price_on`, `price_source`: the last price the user typed or imported
--   from a broker export ('typed', 'fidelity'), theirs alone; only for a covered company. No
--   price is ever fetched for it by Tributary or shared across users.
-- portfolio_lots: each buy (shares, total cost, the day if known: a broker's positions export
--   has none, an optional account label the user types). Never a brokerage account number: the
--   label refuses five digits in a row or six in all (the importer drops Account Number on parse).
-- portfolio_sells: each sale (shares, price a share, day); the realised gain is worked out at read
--   time (query_api/portfolio_maths.py), never stored.
-- journal_entries: dated entries per company (or not-covered symbol): kind, why, what would make
--   the user sell (free text, and a copy of the red lines they tied it to, as they read then),
--   confidence 1-5. An entry can be edited or deleted for 24 hours; after that it is kept as
--   written (a trigger refuses UPDATE and DELETE; restrict_violation) and a change is a new entry
--   that `corrects` it. Cascades (the user or a demo sandbox deleted) are not edits and pass.
--   Holdings and entries are joined by company, so deleting a holding never deletes its journal.
-- notifications.sources (016) gains 'portfolio': open holdings join the filing alerts' follows
--   (query_api/alerts.py AppAlerts.follows), so a held company's new 10-K/10-Q makes an alert, and
--   a review falls due (query_api/portfolio.py).
--
-- Owner-only like saved work and alerts (004, 006, 010, 011, 014, 016, 019): the Query API filters
-- every statement on the JWT `sub`, and the row-level security below is FORCEd so it binds the
-- table owner (the Query API's login) too. Lots and sells reference their holding with the user id
-- in the key, so a row can't point at another user's holding. Everything goes with the user (ON
-- DELETE CASCADE; cascades bypass RLS, as in 004). Not streamed by Debezium (the publication lists
-- only tables that grant access), REPLICA IDENTITY default. Company ids refer to
-- silver.companies in the tributary database (no cross-database FK).
--
-- Lock impact (hermes): CREATE TABLE only for the new tables, plus foreign keys to `users`,
-- `workspaces` and `workspace_items`, each a SHARE ROW EXCLUSIVE lock for the instant it takes to
-- create an empty table: saves of workspaces and items, sign-ups and account changes wait for that
-- instant, reads don't. Replacing notifications' sources CHECK takes an ACCESS EXCLUSIVE lock on
-- notifications until commit and reads its rows once to validate (180 days of alerts: a few
-- thousand rows, milliseconds): the bell and the alerts job wait for that instant. No existing row
-- is rewritten. 5 s at most, then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

CREATE TABLE portfolio_holdings (
    holding_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id     text        CHECK (company_id ~ '^\d{10}$'),
    symbol         text        CHECK (symbol ~ '^[A-Z0-9][A-Z0-9./-]{0,11}$'),
    name           text        CHECK (char_length(name) BETWEEN 1 AND 120
                                      AND name !~ '[[:cntrl:]]'),
    project_id     uuid,
    thesis_item_id uuid,
    price          numeric     CHECK (price > 0 AND price < 1e9),
    price_on       date        CHECK (price_on >= DATE '1990-01-01'),
    price_source   text        CHECK (price_source IN ('typed', 'fidelity')),
    created_at     timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at     timestamptz NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (holding_id, user_id),
    CHECK (company_id IS NOT NULL OR symbol IS NOT NULL),
    CHECK ((price IS NULL) = (price_on IS NULL) AND (price IS NULL) = (price_source IS NULL)),
    CHECK (company_id IS NOT NULL OR price IS NULL),
    CHECK (project_id IS NULL OR thesis_item_id IS NULL),
    FOREIGN KEY (project_id, user_id) REFERENCES workspaces (workspace_id, user_id)
        ON DELETE SET NULL (project_id),
    FOREIGN KEY (thesis_item_id, user_id) REFERENCES workspace_items (item_id, user_id)
        ON DELETE SET NULL (thesis_item_id)
);
CREATE UNIQUE INDEX portfolio_holdings_company ON portfolio_holdings (user_id, company_id)
    WHERE company_id IS NOT NULL;
CREATE UNIQUE INDEX portfolio_holdings_symbol ON portfolio_holdings (user_id, symbol)
    WHERE company_id IS NULL;

CREATE TABLE portfolio_lots (
    lot_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    holding_id    uuid        NOT NULL,
    user_id       text        NOT NULL,
    shares        numeric     NOT NULL CHECK (shares > 0 AND shares < 1e12),
    cost_total    numeric     NOT NULL CHECK (cost_total >= 0 AND cost_total < 1e15),
    bought_on     date        CHECK (bought_on >= DATE '1900-01-01'),
    account_label text        CHECK (char_length(account_label) BETWEEN 1 AND 60
                                     AND account_label !~ '[[:cntrl:]]'
                                     AND account_label !~ '[0-9]{5}'
                                     AND char_length(regexp_replace(account_label, '[^0-9]', '',
                                                                    'g')) < 6),
    source        text        NOT NULL CHECK (source IN ('typed', 'fidelity')),
    created_at    timestamptz NOT NULL DEFAULT statement_timestamp(),
    FOREIGN KEY (holding_id, user_id) REFERENCES portfolio_holdings (holding_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX portfolio_lots_holding ON portfolio_lots (user_id, holding_id);

CREATE TABLE portfolio_sells (
    sell_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    holding_id uuid        NOT NULL,
    user_id    text        NOT NULL,
    shares     numeric     NOT NULL CHECK (shares > 0 AND shares < 1e12),
    price      numeric     NOT NULL CHECK (price >= 0 AND price < 1e9),
    sold_on    date        NOT NULL CHECK (sold_on >= DATE '1900-01-01'),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    FOREIGN KEY (holding_id, user_id) REFERENCES portfolio_holdings (holding_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX portfolio_sells_holding ON portfolio_sells (user_id, holding_id);

CREATE TABLE journal_entries (
    entry_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id text        CHECK (company_id ~ '^\d{10}$'),
    symbol     text        CHECK (symbol ~ '^[A-Z0-9][A-Z0-9./-]{0,11}$'),
    kind       text        NOT NULL CHECK (kind IN ('bought', 'added', 'trimmed', 'sold',
                                                    'review', 'note')),
    decided_on date        NOT NULL CHECK (decided_on >= DATE '1900-01-01'),
    why        text        NOT NULL CHECK (char_length(why) BETWEEN 1 AND 4000),
    sell_rules text        CHECK (char_length(sell_rules) BETWEEN 1 AND 2000),
    red_lines  jsonb       CHECK (jsonb_typeof(red_lines) = 'array'
                                  AND jsonb_array_length(red_lines) <= 10
                                  AND octet_length(red_lines::text) <= 8192),
    confidence smallint    CHECK (confidence BETWEEN 1 AND 5),
    corrects   uuid,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    edited_at  timestamptz,
    UNIQUE (entry_id, user_id),
    CHECK (company_id IS NOT NULL OR symbol IS NOT NULL),
    FOREIGN KEY (corrects, user_id) REFERENCES journal_entries (entry_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX journal_entries_company ON journal_entries (user_id, company_id, created_at);
CREATE INDEX journal_entries_symbol ON journal_entries (user_id, symbol, created_at)
    WHERE company_id IS NULL;

-- 24 hours to fix a slip, then kept as written. A cascade (pg_trigger_depth() > 1: the user or a
-- sandbox deleted, or a corrected entry's corrections going with it) is not an edit.
CREATE FUNCTION journal_entries_kept() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF OLD.created_at < statement_timestamp() - interval '24 hours' THEN
        RAISE EXCEPTION 'a journal entry is kept as written after 24 hours: add a correction'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.entry_id, NEW.user_id, NEW.company_id, NEW.symbol,
                             NEW.corrects, NEW.created_at)
            IS DISTINCT FROM (OLD.entry_id, OLD.user_id, OLD.company_id, OLD.symbol,
                              OLD.corrects, OLD.created_at) THEN
        RAISE EXCEPTION 'an entry keeps its company, what it corrects and when it was written'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$$;
CREATE TRIGGER journal_entries_kept BEFORE UPDATE OR DELETE ON journal_entries
    FOR EACH ROW EXECUTE FUNCTION journal_entries_kept();

ALTER TABLE notifications DROP CONSTRAINT notifications_sources_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_sources_check
    CHECK (sources <@ ARRAY['watchlist', 'analysis', 'portfolio']::text[]
           AND cardinality(sources) BETWEEN 1 AND 3);

ALTER TABLE portfolio_holdings ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_holdings FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON portfolio_holdings
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE portfolio_lots ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_lots FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON portfolio_lots
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE portfolio_sells ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_sells FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON portfolio_sells
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON journal_entries
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
