-- API keys (personal access tokens): a signed-in user's own keys for using Tributary from their
-- scripts, and next from their own AI models through the MCP server. The Query API accepts
-- `Authorization: Bearer trb_...` beside its JWT (query_api/api_keys.py, README "API keys").
--
-- api_keys: one row per key. The key itself ("trb_" + 32 random bytes, base64url: 47
--   characters) is shown once, when it is created, and never stored: only its SHA-256
--   (`key_hash`) and its first 8 characters (`prefix`, "trb_" + 4, so the owner can tell keys
--   apart). A key belongs to one user; requests made with it are that user's, through the same
--   two permission layers as the web app (OpenFGA first, then row-level security as the user).
--   `scopes` is {read} (data and the user's own saved work) or {read,write} (also creating and
--   editing the user's own saved work and watchlist); no key manages the account (keys,
--   passwords, webhooks, alert settings stay sign-in only). Every key expires (at most 366 days
--   after it was made: the API offers 30, 90 or 365). Revoking sets `revoked_at`; the row stays
--   so its owner still sees its usage, and the API prunes keys revoked or expired more than 30
--   days ago when the owner makes a new one. Demo sandboxes (user ids 'demo-...') can't have keys.
-- api_key_usage: per key per UTC day, the requests the key made (`requests`) and the ones
--   refused for a limit (`limited`); the same row holds the current minute's count (`minute`,
--   `minute_requests`), so one atomic upsert per request enforces both limits (60 a minute,
--   10,000 a day by default) across every Query API replica.
--
-- Owner-only like saved work and alerts (004, 006, 010, 011, 014, 016): the Query API filters
-- every statement on the user, and the row-level security below is FORCEd so it binds the table
-- owner (the Query API's login) too. The one read before the API knows the user is a request's
-- own key, looked up by its hash: the `key_holder` policy lets a transaction that set
-- `app.api_key_hash` to a key's hash read that one row (SELECT only). Knowing the hash means
-- knowing the key, so it reveals nothing the caller didn't send. Usage rows reference their key
-- with the user id in the foreign key, so a row can't point at another user's key. Everything
-- goes with the user (ON DELETE CASCADE; cascades bypass RLS, as in 004). Not streamed by
-- Debezium: a key grants nothing beyond what its user already reads (streaming/debezium's
-- include list names its tables), REPLICA IDENTITY default.
--
-- Lock impact (hermes): CREATE TABLE only, plus a foreign key to `users`, which takes a SHARE ROW
-- EXCLUSIVE lock on users for the instant it takes to create the empty table: sign-ups and
-- account changes wait for that instant (CDC reads are not blocked), reads don't. No existing
-- row is read or rewritten. 5 s at most, then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

CREATE TABLE api_keys (
    key_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      text        NOT NULL REFERENCES users ON DELETE CASCADE
                             CHECK (user_id NOT LIKE 'demo-%'),
    name         text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60
                                             AND name = btrim(name)
                                             AND name !~ '[[:cntrl:]]'),
    prefix       text        NOT NULL CHECK (prefix ~ '^trb_[A-Za-z0-9_-]{4}$'),
    key_hash     bytea       NOT NULL UNIQUE CHECK (octet_length(key_hash) = 32),
    scopes       text[]      NOT NULL CHECK (scopes = ARRAY['read']
                                             OR scopes = ARRAY['read', 'write']),
    created_at   timestamptz NOT NULL DEFAULT statement_timestamp(),
    expires_at   timestamptz NOT NULL,
    last_used_at timestamptz,
    revoked_at   timestamptz,
    UNIQUE (key_id, user_id),
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '366 days'),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX api_keys_user ON api_keys (user_id, created_at);

CREATE TABLE api_key_usage (
    key_id          uuid        NOT NULL,
    user_id         text        NOT NULL,
    day             date        NOT NULL,
    requests        integer     NOT NULL DEFAULT 0 CHECK (requests >= 0),
    limited         integer     NOT NULL DEFAULT 0 CHECK (limited >= 0),
    minute          timestamptz NOT NULL,
    minute_requests integer     NOT NULL DEFAULT 0 CHECK (minute_requests >= 0),
    PRIMARY KEY (key_id, day),
    FOREIGN KEY (key_id, user_id) REFERENCES api_keys (key_id, user_id) ON DELETE CASCADE
);
CREATE INDEX api_key_usage_user ON api_key_usage (user_id, day);

ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON api_keys
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
-- the request's own key, by its hash, before the user is known (read only)
CREATE POLICY key_holder ON api_keys FOR SELECT
    USING (key_hash = decode(nullif(current_setting('app.api_key_hash', true), ''), 'hex'));

ALTER TABLE api_key_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_key_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON api_key_usage
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
