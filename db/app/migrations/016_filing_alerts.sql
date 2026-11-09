-- Filing alerts (F6, Phase 9 E): when a company a user watches (watchlists) or analyses (an
-- analysis project, 012) files a 10-K, a 10-Q or an 8-K with a material item, the user gets a
-- notification in the app, optionally sent on to a webhook of theirs, with the filing's key
-- numbers and the user's red lines evaluated on it. The Query API's `query-api-alerts` job
-- (a CronJob, query_api/alert_job.py) writes them; the Query API's /v1/alerts routes read them
-- and the user's settings.
--
-- alert_settings: which forms a user wants for a company (no row = every form). Rows can stay for
--   a company the user no longer follows; only followed companies are alerted.
-- notifications: one per (user, filing, company), so a run that sees a filing again changes
--   nothing (idempotent on the filing). `status`: pending (a 10-K/10-Q whose figures aren't in
--   gold yet, an 8-K whose items aren't known yet), ready (complete: shown and sent), skipped (an
--   8-K whose items are all routine: kept so it isn't made again, never shown). `detail` holds the
--   key numbers and the red-line results as computed for this user, under their access, when it
--   became ready; the API strips them again for a company the user can no longer read.
-- red_lines: a user's rule on a company's figures: a formula in the Query API's formula language
--   (query_api/formula: catalog metrics, + - * /, lags, ttm, abs; checked and compiled safely),
--   one comparison operator and a constant. Evaluated on each new 10-K/10-Q and on demand.
-- webhooks: where a user's alerts are also sent (https only; the Query API refuses private,
--   loopback, link-local and metadata addresses after resolving the name, and again before each
--   send, and never follows a redirect). The URL is the user's own (a Discord or Slack webhook URL
--   carries its token, so the API returns it masked). No signing secret is stored: it is derived
--   from the API's JWT secret, the webhook id and `secret_salt` (query_api/webhooks.py), shown
--   once when created or rotated, never logged or returned again.
-- webhook_deliveries: the delivery log, one row per (webhook, notification), retried with backoff
--   by the job; `last_error` is a short code, never a message, a URL or a response body.
--   notification_id NULL is a test send from the settings page.
-- alert_checks: when the job last looked for a user's new filings (the Monitor step's "last
--   check").
--
-- Owner-only like saved work (004, 006, 010, 011, 014): the Query API filters every statement on
-- the JWT `sub` (the job on the user it is working for), and the row-level security below is
-- FORCEd so it binds the table owner (the Query API's login) too. Deliveries reference their
-- webhook and notification with the user id in the key, so a row can't point at another user's.
-- Everything goes with the user (ON DELETE CASCADE; cascades bypass RLS, as in 004). Not streamed
-- by Debezium: they grant nothing (an alert is about a company's public filing; its figures were
-- read through the permission-checked Store as that user). Company ids refer to silver.companies
-- in the tributary database (no cross-database FK).
--
-- Lock impact (hermes): CREATE TABLE only, plus foreign keys to `users`, which take a SHARE ROW
-- EXCLUSIVE lock on users for the instant it takes to create each empty table: sign-ups and
-- account changes wait for that instant (CDC reads are not blocked), reads don't. No existing row
-- is read or rewritten. 5 s at most, then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

CREATE TABLE alert_settings (
    user_id    text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id text        NOT NULL CHECK (company_id ~ '^\d{10}$'),
    forms      text[]      NOT NULL CHECK (forms <@ ARRAY['10-K', '10-Q', '8-K']::text[]
                                           AND cardinality(forms) <= 3),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id)
);

CREATE TABLE notifications (
    notification_id uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id      text        NOT NULL CHECK (company_id ~ '^\d{10}$'),
    accession_no    text        NOT NULL CHECK (accession_no ~ '^\d{10}-\d{2}-\d{6}$'),
    form            text        NOT NULL CHECK (form IN ('10-K', '10-Q', '8-K')),
    filed           date        NOT NULL,
    period_end      date,
    items           text[]      CHECK (cardinality(items) <= 40),  -- an 8-K's; NULL: not known
    sources         text[]      NOT NULL CHECK (sources <@ ARRAY['watchlist', 'analysis']::text[]
                                                AND cardinality(sources) BETWEEN 1 AND 2),
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'ready', 'skipped')),
    detail          jsonb       NOT NULL DEFAULT '{}'
                                CHECK (jsonb_typeof(detail) = 'object'
                                       AND octet_length(detail::text) <= 16384),
    created_at      timestamptz NOT NULL DEFAULT statement_timestamp(),
    ready_at        timestamptz,
    read_at         timestamptz,
    UNIQUE (user_id, accession_no, company_id),
    UNIQUE (notification_id, user_id)
);
CREATE INDEX notifications_user ON notifications (user_id, created_at DESC);
CREATE INDEX notifications_company ON notifications (user_id, company_id, created_at DESC);
CREATE INDEX notifications_pending ON notifications (user_id) WHERE status = 'pending';

CREATE TABLE red_lines (
    rule_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id text        NOT NULL CHECK (company_id ~ '^\d{10}$'),
    label      text        NOT NULL CHECK (char_length(label) BETWEEN 1 AND 120),
    formula    text        NOT NULL CHECK (char_length(formula) BETWEEN 1 AND 500),
    view       text        NOT NULL CHECK (view IN ('quarter', 'ttm')),
    op         text        NOT NULL CHECK (op IN ('<', '<=', '>', '>=')),
    threshold  numeric     NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX red_lines_user ON red_lines (user_id, company_id, created_at);

CREATE TABLE webhooks (
    webhook_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text        NOT NULL REFERENCES users ON DELETE CASCADE,
    url         text        NOT NULL CHECK (char_length(url) BETWEEN 12 AND 2048
                                            AND url LIKE 'https://%'),
    format      text        NOT NULL CHECK (format IN ('json', 'discord', 'slack')),
    secret_salt bytea       NOT NULL CHECK (octet_length(secret_salt) = 16),
    enabled     boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT statement_timestamp(),
    rotated_at  timestamptz,
    UNIQUE (webhook_id, user_id)
);
CREATE INDEX webhooks_user ON webhooks (user_id, created_at);

CREATE TABLE webhook_deliveries (
    delivery_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         text        NOT NULL,
    webhook_id      uuid        NOT NULL,
    notification_id uuid,
    status          text        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts        smallint    NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 20),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz,
    last_status     smallint    CHECK (last_status BETWEEN 100 AND 599),
    last_error      text        CHECK (last_error ~ '^[a-z0-9_]{1,40}$'),
    created_at      timestamptz NOT NULL DEFAULT statement_timestamp(),
    delivered_at    timestamptz,
    FOREIGN KEY (webhook_id, user_id) REFERENCES webhooks (webhook_id, user_id)
        ON DELETE CASCADE,
    FOREIGN KEY (notification_id, user_id) REFERENCES notifications (notification_id, user_id)
        ON DELETE CASCADE,
    UNIQUE (webhook_id, notification_id)
);
CREATE INDEX webhook_deliveries_due ON webhook_deliveries (user_id, next_attempt_at)
    WHERE status = 'pending';
CREATE INDEX webhook_deliveries_user ON webhook_deliveries (user_id, created_at DESC);

CREATE TABLE alert_checks (
    user_id         text        PRIMARY KEY REFERENCES users ON DELETE CASCADE,
    checked_at      timestamptz NOT NULL,
    filings_through date
);

ALTER TABLE alert_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON alert_settings
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON notifications
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE red_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE red_lines FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON red_lines
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE webhooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhooks FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON webhooks
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON webhook_deliveries
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE alert_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_checks FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON alert_checks
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
