-- Owner-created username/password accounts (query_api/passwords.py): there is no public
-- signup; the owner creates each account with `query-api-account create` inside the Query API
-- pod. The user is an ordinary row in users (user_id 'pw-<username>', email
-- '<username>@password.invalid') with a pro subscription and, while the public catalog is
-- closed, the same company grants as a GitHub account (sandbox.grant_demo_access), all written
-- in the same transaction as this row; CDC streams those to authz-sync as usual. This table
-- only says which username and password the user signs in with: it grants nothing, so
-- Debezium doesn't stream it (identity, not access). Goes with the user (ON DELETE CASCADE).
--
-- password_hash is self-describing ('scrypt$n=..,r=..,p=..$<salt>$<hash>', base64), so the
-- parameters can be raised later and old hashes still verify. The plain password is never
-- stored or logged anywhere.
--
-- questions_per_day: this account's own daily assistant cap (demo_agent_usage, migration 003);
-- it is not counted against the demo's shared per-day cap (demo_agent_daily).
-- failed_attempts / locked_until: consecutive failed sign-ins and the lockout they trigger.
-- sessions_valid_after: tokens issued before it are refused (set by set-password and disable).

CREATE TABLE password_accounts (
    user_id              text        PRIMARY KEY REFERENCES users ON DELETE CASCADE,
    username             text        NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9_.-]{3,32}$'),
    password_hash        text        NOT NULL
        CHECK (password_hash ~ '^scrypt\$n=[0-9]+,r=[0-9]+,p=[0-9]+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$'),
    questions_per_day    integer     NOT NULL CHECK (questions_per_day BETWEEN 0 AND 1000),
    disabled             boolean     NOT NULL DEFAULT false,
    failed_attempts      integer     NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
    locked_until         timestamptz,
    sessions_valid_after timestamptz NOT NULL,
    created_at           timestamptz NOT NULL,
    last_sign_in_at      timestamptz,
    CHECK (user_id = 'pw-' || username)
);
