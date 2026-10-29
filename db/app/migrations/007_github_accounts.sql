-- Sign in with GitHub (query_api/github.py): a GitHub identity → its permanent app user.
-- The user is an ordinary row in users (user_id 'gh-<GitHub id>', email GitHub's noreply
-- address) with a subscription and company grants like a demo sandbox's, which CDC streams to
-- authz-sync as usual. This table only says which GitHub account the user is: it grants
-- nothing, so Debezium doesn't stream it (identity, not access). No GitHub access token is
-- ever stored, here or anywhere. A user with a row here is under the demo's assistant caps
-- (demo_agent_usage / demo_agent_daily, migration 003) but is not a sandbox: there is no
-- demo_sandboxes row, so demo-cleanup never deletes it. Goes with the user (ON DELETE CASCADE).

CREATE TABLE github_accounts (
    github_id       bigint      PRIMARY KEY CHECK (github_id > 0),
    user_id         text        NOT NULL UNIQUE REFERENCES users ON DELETE CASCADE,
    -- the public login at the latest sign-in (GitHub logins can be renamed; the id can't)
    login           text        NOT NULL CHECK (login ~ '^[A-Za-z0-9-]{1,39}$'),
    created_at      timestamptz NOT NULL,
    last_sign_in_at timestamptz NOT NULL,
    CHECK (user_id = 'gh-' || github_id::text)
);
