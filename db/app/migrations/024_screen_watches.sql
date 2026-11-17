-- Saved screens that alert (Direction A): a user watches one of their saved screens (a workspace
-- item of kind `screen`, 004) and hears when a company enters or leaves it, or when a column's
-- value crosses a line they set (for one named company or any company in the screen), once the
-- nightly figures land. The Query API's alerts job (query-api-alerts, query_api/watch_job.py)
-- evaluates each watched screen AS ITS OWNER (OpenFGA, then RLS: only companies the owner can
-- read count) and writes the alerts; the in-app bell, the user's webhooks and email (once it is
-- on) carry them like filing alerts (016).
--
-- watches: one per saved screen (UNIQUE (user_id, item_id)). `source` is what is watched:
--   'screen' today; a saved SQL query (B3b) is meant to become another source with its own
--   reference columns, the rest unchanged. `columns`: the screener columns the screen was shown
--   with when the watch was saved (a company with no figure in any of them isn't listed, the
--   screener's rule). `lines`: up to 5 {column, direction above|below, threshold, company_id or
--   null for any company in the screen}. What the job saw last, owner-only: `members` (company
--   ids, at most 500: a broader screen is `too_broad` and alerts nothing), `near` (per line, the
--   company ids whose value was readable and on the near side of the line: a crossing is a company
--   there last time and past the line now), `basis` (a hash of the screen's definition, the watch's
--   columns and lines and the owner's access: when it changes, the next check only records), when
--   it checked and how it went. No values are stored.
-- screen_alerts: one per watch per check that found something: the screen's name then and
--   `detail` (which companies entered, left, crossed which line with what value, at most 20
--   listed and "and N more"), read and kept like notifications (KEEP_DAYS).
-- webhook_deliveries.screen_alert_id: a delivery is for a filing notification or a screen alert
--   (never both); with neither it is a test send, as before.
--
-- Owner-only like every alert table (016): the Query API filters every statement on the JWT `sub`
-- (the job on the user it works for) and the row-level security below is FORCEd so it binds the
-- tables' owner too. A watch references its screen with the user id in the key, so it can't point
-- at another user's item, and goes with it (ON DELETE CASCADE); an alert goes with its watch.
-- Not streamed by Debezium: nothing here grants access.
--
-- Lock impact (hermes): CREATE TABLE with foreign keys takes a SHARE ROW EXCLUSIVE lock on
-- workspace_items for the instant it takes to create the empty table (item saves wait for it,
-- reads don't). The ALTERs on webhook_deliveries take an ACCESS EXCLUSIVE lock and read its rows
-- once to validate (a few hundred rows: milliseconds); the alerts job's deliveries and the delivery
-- log wait for that instant. 5 s at most, then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

CREATE TABLE watches (
    watch_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    text        NOT NULL,
    source     text        NOT NULL DEFAULT 'screen' CHECK (source IN ('screen')),
    item_id    uuid        NOT NULL,
    entering   boolean     NOT NULL DEFAULT true,
    leaving    boolean     NOT NULL DEFAULT true,
    columns    text[]      NOT NULL DEFAULT '{}' CHECK (cardinality(columns) <= 12),
    lines      jsonb       NOT NULL DEFAULT '[]'
                           CHECK (jsonb_typeof(lines) = 'array' AND jsonb_array_length(lines) <= 5
                                  AND octet_length(lines::text) <= 4096),
    basis      text        CHECK (basis ~ '^[0-9a-f]{64}$'),
    members    text[]      CHECK (cardinality(members) <= 500),
    near       jsonb       CHECK (jsonb_typeof(near) = 'object'
                                  AND octet_length(near::text) <= 65536),
    checked_at timestamptz,
    outcome    text        CHECK (outcome IN ('baseline', 'ok', 'too_broad', 'failed')),
    companies  integer     CHECK (companies >= 0),  -- in the screen at the last check
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (user_id, item_id),
    UNIQUE (watch_id, user_id),
    FOREIGN KEY (item_id, user_id) REFERENCES workspace_items (item_id, user_id)
        ON DELETE CASCADE
);

CREATE TABLE screen_alerts (
    alert_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    text        NOT NULL,
    watch_id   uuid        NOT NULL,
    item_id    uuid        NOT NULL,
    name       text        NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    detail     jsonb       NOT NULL CHECK (jsonb_typeof(detail) = 'object'
                                           AND octet_length(detail::text) <= 16384),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    read_at    timestamptz,
    UNIQUE (alert_id, user_id),
    FOREIGN KEY (watch_id, user_id) REFERENCES watches (watch_id, user_id) ON DELETE CASCADE
);
CREATE INDEX screen_alerts_user ON screen_alerts (user_id, created_at DESC);
CREATE INDEX screen_alerts_watch ON screen_alerts (watch_id, created_at DESC);

ALTER TABLE webhook_deliveries ADD COLUMN screen_alert_id uuid;
ALTER TABLE webhook_deliveries ADD CONSTRAINT webhook_deliveries_screen_alert_fkey
    FOREIGN KEY (screen_alert_id, user_id) REFERENCES screen_alerts (alert_id, user_id)
    ON DELETE CASCADE;
ALTER TABLE webhook_deliveries ADD CONSTRAINT webhook_deliveries_one_subject
    CHECK (notification_id IS NULL OR screen_alert_id IS NULL);
ALTER TABLE webhook_deliveries ADD CONSTRAINT webhook_deliveries_webhook_screen_alert
    UNIQUE (webhook_id, screen_alert_id);

ALTER TABLE watches ENABLE ROW LEVEL SECURITY;
ALTER TABLE watches FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON watches
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));

ALTER TABLE screen_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE screen_alerts FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON screen_alerts
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
