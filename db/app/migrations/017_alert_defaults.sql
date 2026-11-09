-- Filing alerts, pre-ship (2026-11-09): the 8-K default and webhooks that keep failing.
--
-- alert_settings.other_8ks: an 8-K alerts by default only with a material item (a deal, debt,
--   leadership, results under 2.02, an impairment, non-reliance, ...); one whose only items are
--   7.01 (Regulation FD) and/or 8.01 (Other events), with or without exhibits or other routine
--   items, doesn't. `other_8ks = true` turns those on for that company (query_api/alerts.py has
--   the rule). Every existing row keeps its forms and reads false: the new default, as for a
--   company without a row.
-- webhooks.stalls / stalled_until: a webhook whose sends keep stalling (no answer in time, a name
--   that doesn't resolve or resolves too slowly, no connection, a failed TLS handshake) is left out
--   of the alerts job's sends until `stalled_until` (longer after each consecutive stall,
--   query_api/alert_job.py STALL_WAITS), so a dead endpoint can't spend the run's shared sending
--   time every ten minutes. A send it answers (any HTTP status), a test send that arrives or
--   turning it back on clears both. Its pending deliveries stay due and are sent once it's tried
--   again.
--
-- Same owner-only rules as 016 (forced row-level security on app.user_id, not streamed): new
-- columns on existing tables need no policy.
--
-- Lock impact (hermes): three ADD COLUMNs with constant defaults or none: no rewrite (Postgres 11+
-- keeps a constant default in the catalog); the CHECK on `stalls` reads webhooks' rows once to
-- validate (a handful). Each ALTER takes an ACCESS EXCLUSIVE lock on alert_settings or webhooks
-- until commit: the settings page and the alerts job wait for that instant, nothing else touches
-- these tables. The lock waits for open transactions on the table and everything queues behind it
-- while it waits: 5 s at most, then the deploy hook fails and is re-run.

SET LOCAL lock_timeout = '5s';

ALTER TABLE alert_settings
    ADD COLUMN other_8ks boolean NOT NULL DEFAULT false;

ALTER TABLE webhooks
    ADD COLUMN stalls smallint NOT NULL DEFAULT 0 CHECK (stalls BETWEEN 0 AND 1000),
    ADD COLUMN stalled_until timestamptz;
