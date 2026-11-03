-- Connect your data (Phase 9 C): a user's own market-data API key and the prices fetched with
-- it. Prices can't be shown publicly (vendor licences allow personal use, not
-- redistribution), so each user brings their own key; Tributary fetches with that key, keeps
-- the data for that user only and shows it only to them. No shared cache across users.
--
-- data_connections: one row per (user, provider). The key is stored only encrypted, with an
-- envelope scheme (query_api/keybox.py): a random 256-bit data key encrypts the API key with
-- AES-256-GCM (`nonce`, `ciphertext`), and the master key named by `key_id` (Secret key
-- data-connections-key, never in this database) wraps the data key with AES-256-GCM
-- (`wrapped_key` = its own 12-byte nonce + 32-byte key + 16-byte tag). Both use the user id and
-- provider as associated data, so a ciphertext copied to another user's row fails to decrypt.
-- `last4` is what the UI shows ("...a1b2"); nothing else about the key is readable here.
--
-- market_prices: end-of-day prices per (user, provider, company, day), fetched on demand with
-- that user's key. market_price_fetches: when each (user, provider, company) was last asked
-- (the once-a-day freshness window) and the ticker used. Both reference the connection and go
-- with it (ON DELETE CASCADE): disconnecting deletes the key and that user's cached prices in
-- one statement; deleting the user deletes everything.
--
-- Owner-only like saved work (004, 006, 010): the Query API filters every statement on the JWT
-- `sub`, and the row-level security below is FORCEd so it binds the table owner (the Query
-- API's login) too. Not streamed by Debezium: they grant nothing. The company id refers to
-- silver.companies in the tributary database (no cross-database FK).

CREATE TABLE data_connections (
    user_id      text        NOT NULL REFERENCES users ON DELETE CASCADE,
    provider     text        NOT NULL CHECK (provider IN ('tiingo')),
    key_id       text        NOT NULL CHECK (key_id ~ '^[A-Za-z0-9_-]{1,16}$'),
    wrapped_key  bytea       NOT NULL CHECK (octet_length(wrapped_key) = 60),
    nonce        bytea       NOT NULL CHECK (octet_length(nonce) = 12),
    -- an API key of at most 256 bytes + the 16-byte GCM tag
    ciphertext   bytea       NOT NULL CHECK (octet_length(ciphertext) BETWEEN 17 AND 272),
    last4        text        NOT NULL CHECK (last4 ~ '^[A-Za-z0-9]{0,4}$'),
    created_at   timestamptz NOT NULL DEFAULT now(),
    validated_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE market_prices (
    user_id    text    NOT NULL,
    provider   text    NOT NULL,
    company_id text    NOT NULL CHECK (company_id ~ '^\d{10}$'),
    day        date    NOT NULL,
    close      numeric NOT NULL CHECK (close > 0),
    adj_close  numeric CHECK (adj_close > 0),
    volume     bigint  CHECK (volume >= 0),
    PRIMARY KEY (user_id, provider, company_id, day),
    FOREIGN KEY (user_id, provider) REFERENCES data_connections ON DELETE CASCADE
);

CREATE TABLE market_price_fetches (
    user_id    text        NOT NULL,
    provider   text        NOT NULL,
    company_id text        NOT NULL CHECK (company_id ~ '^\d{10}$'),
    ticker     text        NOT NULL CHECK (ticker ~ '^[A-Z0-9.-]{1,12}$'),
    status     text        NOT NULL CHECK (status IN ('ok', 'not_found')),
    fetched_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, provider, company_id),
    FOREIGN KEY (user_id, provider) REFERENCES data_connections ON DELETE CASCADE
);

ALTER TABLE data_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_connections FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON data_connections
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE market_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_prices FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON market_prices
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE market_price_fetches ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_price_fetches FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON market_price_fetches
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
