-- Demo sandboxes: "Try the demo" creates a real, temporary user (users + subscriptions +
-- company_grants, streamed by CDC like any other user, so authz-sync grants and later revokes
-- access). These tables only describe the sandbox; none of them grants access, so none is
-- streamed by Debezium. Everything here goes with the user (ON DELETE CASCADE): the hourly
-- demo-cleanup job deletes expired sandbox users, which revokes their access through CDC.

CREATE TABLE demo_sandboxes (
    user_id    text        PRIMARY KEY REFERENCES users ON DELETE CASCADE,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);
CREATE INDEX demo_sandboxes_expires ON demo_sandboxes (expires_at);

-- Creation rate limit per client: ip_key is an HMAC of the address, never the address itself.
-- Kept a day, then pruned by demo-cleanup. No user reference: limits outlive the sandbox.
CREATE TABLE demo_creations (
    ip_key     text        NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE INDEX demo_creations_ip ON demo_creations (ip_key, created_at);

-- Assistant questions per sandbox per UTC day (per-user cap).
CREATE TABLE demo_agent_usage (
    user_id   text    NOT NULL REFERENCES users ON DELETE CASCADE,
    day       date    NOT NULL,
    questions integer NOT NULL CHECK (questions >= 0),
    PRIMARY KEY (user_id, day)
);

-- Assistant questions from all sandboxes per UTC day (global cap). Not tied to a user, so
-- deleting expired sandboxes doesn't hand the day's budget back.
CREATE TABLE demo_agent_daily (
    day       date    PRIMARY KEY,
    questions integer NOT NULL CHECK (questions >= 0)
);
