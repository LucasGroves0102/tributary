-- Connected apps (MCP phase 3): Tributary's OAuth 2.1 authorization server, so a user adds
-- Tributary to claude.ai or ChatGPT by URL, signs in, approves, and the app reads Tributary as
-- them through the MCP server, with no key to copy (query_api/oauth.py, Query API README
-- "Connected apps (OAuth)").
--
-- oauth_clients: the apps that may ask. Either one that registered itself (RFC 7591 dynamic
--   client registration: `client_id` 'trc_' + random, kind 'registered') or one identified by
--   its client ID metadata document (the MCP spec's preferred way: `client_id` is the https URL
--   of a JSON document the app publishes, kind 'metadata_document', fetched and cached here,
--   `refreshed_at` / `fresh_until`). `redirect_uris` are exact (https, or http on a loopback
--   address for apps on the user's own computer). A registered client that asked for a secret
--   has only its SHA-256 here (`secret_hash`); the secret was shown once, in the registration
--   answer. No user data: no row-level security (like password_accounts, 013).
-- oauth_grants: one per approval ("Connect" on the consent screen): which user let which app
--   read as them, the scopes ({read}: the read routes an API key with the read scope reaches)
--   and the resource (the MCP server's URL, RFC 8707) the tokens are for; `consent_hash` (the
--   SHA-256 of the consent request's browser nonce) makes each request approvable once. Revoking a grant
--   (Settings, Disconnect; refresh-token reuse; the revocation endpoint) ends every token it
--   issued at once. Demo sandboxes can't connect apps (a CHECK too).
-- oauth_codes: authorization codes, single-use, 60 seconds, bound to the grant (so the client
--   and user), the redirect URI and the PKCE S256 challenge. Only the code's SHA-256 is kept.
-- oauth_tokens: access tokens (1 hour, 'tro_' + 32 random bytes) and refresh tokens (30 days,
--   'trr_' + 32 random bytes, rotated on every use: `used_at` set, and a used one presented
--   again revokes its grant). Only SHA-256 hashes and the first 8 characters are kept.
-- oauth_usage: per grant per UTC day, requests and those refused for a limit, plus the current
--   minute's count: the same limits as an API key (019's api_key_usage), one upsert per request.
--
-- Owner-only like api_keys (019): grants, codes, tokens and usage have FORCEd row-level security
-- on `app.user_id`, which binds the tables' owner (the Query API's login) too. The two reads
-- before the API knows the user are by hash: `code_holder` (a transaction that set
-- `app.oauth_code_hash` reads that one code) and `token_holder` (`app.oauth_token_hash`, that one
-- token), SELECT only; knowing the hash means holding the code or token. Codes, tokens and usage
-- reference their grant with the user id in the key, so a row can't point at another user's
-- grant. Everything goes with the user or the client (ON DELETE CASCADE; cascades bypass RLS,
-- as in 004). Not streamed by Debezium: a connected app reads nothing its user doesn't.
--
-- Lock impact (hermes): CREATE TABLE only, plus foreign keys to `users` (and to the new tables),
-- which take a SHARE ROW EXCLUSIVE lock on users for the instant it takes to create each empty
-- table: sign-ups and account changes wait for that instant (CDC reads are not blocked), reads
-- don't. No existing row is read or rewritten. 5 s at most, then the deploy hook fails and is
-- re-run.

SET LOCAL lock_timeout = '5s';

CREATE TABLE oauth_clients (
    client_key    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     text        NOT NULL UNIQUE CHECK (char_length(client_id) BETWEEN 1 AND 2048),
    kind          text        NOT NULL CHECK (kind IN ('registered', 'metadata_document')),
    client_name   text        NOT NULL CHECK (char_length(client_name) BETWEEN 1 AND 100
                                              AND client_name !~ '[[:cntrl:]]'),
    client_uri    text        CHECK (client_uri IS NULL OR char_length(client_uri) <= 2048),
    redirect_uris text[]      NOT NULL CHECK (cardinality(redirect_uris) BETWEEN 1 AND 10),
    auth_method   text        NOT NULL CHECK (auth_method IN ('none', 'client_secret_basic',
                                                              'client_secret_post')),
    secret_hash   bytea       CHECK (secret_hash IS NULL OR octet_length(secret_hash) = 32),
    created_at    timestamptz NOT NULL,
    refreshed_at  timestamptz NOT NULL,
    fresh_until   timestamptz,
    last_used_at  timestamptz,
    -- a secret exactly when the client authenticates with one; metadata documents never do
    CHECK ((auth_method = 'none') = (secret_hash IS NULL)),
    CHECK (kind = 'registered' OR auth_method = 'none'),
    CHECK ((kind = 'metadata_document') = (fresh_until IS NOT NULL)),
    CHECK (kind = 'registered' OR client_id LIKE 'https://%'),
    CHECK (kind = 'metadata_document' OR client_id ~ '^trc_[A-Za-z0-9_-]{32}$')
);
CREATE INDEX oauth_clients_created ON oauth_clients (kind, created_at);

CREATE TABLE oauth_grants (
    grant_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      text        NOT NULL REFERENCES users ON DELETE CASCADE
                             CHECK (user_id NOT LIKE 'demo-%'),
    client_key   uuid        NOT NULL REFERENCES oauth_clients ON DELETE CASCADE,
    scopes       text[]      NOT NULL CHECK (scopes = ARRAY['read']),
    resource     text        NOT NULL CHECK (char_length(resource) BETWEEN 1 AND 2048),
    consent_hash bytea       NOT NULL UNIQUE CHECK (octet_length(consent_hash) = 32),
    created_at   timestamptz NOT NULL,
    last_used_at timestamptz,
    revoked_at   timestamptz,
    UNIQUE (grant_id, user_id),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX oauth_grants_user ON oauth_grants (user_id, client_key);
CREATE INDEX oauth_grants_client ON oauth_grants (client_key);

CREATE TABLE oauth_codes (
    code_hash      bytea       PRIMARY KEY CHECK (octet_length(code_hash) = 32),
    grant_id       uuid        NOT NULL,
    user_id        text        NOT NULL,
    redirect_uri   text        NOT NULL CHECK (char_length(redirect_uri) BETWEEN 1 AND 2048),
    code_challenge text        NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
    created_at     timestamptz NOT NULL,
    expires_at     timestamptz NOT NULL,
    used_at        timestamptz,
    FOREIGN KEY (grant_id, user_id) REFERENCES oauth_grants (grant_id, user_id)
        ON DELETE CASCADE,
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '60 seconds')
);
CREATE INDEX oauth_codes_user ON oauth_codes (user_id, expires_at);

CREATE TABLE oauth_tokens (
    token_hash bytea       PRIMARY KEY CHECK (octet_length(token_hash) = 32),
    grant_id   uuid        NOT NULL,
    user_id    text        NOT NULL,
    kind       text        NOT NULL CHECK (kind IN ('access', 'refresh')),
    prefix     text        NOT NULL CHECK (prefix ~ '^tr[or]_[A-Za-z0-9_-]{4}$'),
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    FOREIGN KEY (grant_id, user_id) REFERENCES oauth_grants (grant_id, user_id)
        ON DELETE CASCADE,
    CHECK (expires_at > created_at),
    CHECK (kind = 'refresh' OR (expires_at <= created_at + interval '1 hour'
                                AND used_at IS NULL)),
    CHECK (kind = 'access' OR expires_at <= created_at + interval '30 days'),
    CHECK ((kind = 'access') = (prefix LIKE 'tro\_%'))
);
CREATE INDEX oauth_tokens_grant ON oauth_tokens (grant_id, kind, expires_at);

CREATE TABLE oauth_usage (
    grant_id        uuid        NOT NULL,
    user_id         text        NOT NULL,
    day             date        NOT NULL,
    requests        integer     NOT NULL DEFAULT 0 CHECK (requests >= 0),
    limited         integer     NOT NULL DEFAULT 0 CHECK (limited >= 0),
    minute          timestamptz NOT NULL,
    minute_requests integer     NOT NULL DEFAULT 0 CHECK (minute_requests >= 0),
    PRIMARY KEY (grant_id, day),
    FOREIGN KEY (grant_id, user_id) REFERENCES oauth_grants (grant_id, user_id)
        ON DELETE CASCADE
);
CREATE INDEX oauth_usage_user ON oauth_usage (user_id, day);

ALTER TABLE oauth_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON oauth_grants
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE oauth_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_codes FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON oauth_codes
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
-- the code a token request presents, by its hash, before the user is known (read only)
CREATE POLICY code_holder ON oauth_codes FOR SELECT
    USING (code_hash = decode(nullif(current_setting('app.oauth_code_hash', true), ''), 'hex'));

ALTER TABLE oauth_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_tokens FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON oauth_tokens
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
-- the token a request or a refresh presents, by its hash, before the user is known (read only)
CREATE POLICY token_holder ON oauth_tokens FOR SELECT
    USING (token_hash = decode(nullif(current_setting('app.oauth_token_hash', true), ''), 'hex'));

ALTER TABLE oauth_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE oauth_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON oauth_usage
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
