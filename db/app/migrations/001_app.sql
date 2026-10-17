-- App database: who can see what. Changes stream to Kafka via Debezium (wal_level=logical).

CREATE TABLE organizations (
    org_id     text        PRIMARY KEY,
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    user_id    text        PRIMARY KEY,
    email      text        NOT NULL UNIQUE,
    org_id     text        REFERENCES organizations ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Plans grant data tiers: financials (gold.quarterly_financials), filings (gold.filing_sections).
CREATE TABLE plans (
    plan_id text PRIMARY KEY,
    name    text NOT NULL
);

CREATE TABLE plan_tiers (
    plan_id text NOT NULL REFERENCES plans ON DELETE CASCADE,
    tier    text NOT NULL CHECK (tier IN ('financials', 'filings')),
    PRIMARY KEY (plan_id, tier)
);

INSERT INTO plans VALUES ('free', 'Free'), ('pro', 'Pro');
INSERT INTO plan_tiers VALUES ('free', 'financials'), ('pro', 'financials'), ('pro', 'filings');

-- One active plan per user; no row means no tiers at all.
CREATE TABLE subscriptions (
    user_id    text        PRIMARY KEY REFERENCES users ON DELETE CASCADE,
    plan_id    text        NOT NULL REFERENCES plans,
    started_at timestamptz NOT NULL DEFAULT now()
);

-- A company granted to one user or to every member of an organization.
CREATE TABLE company_grants (
    grant_id   bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id text        NOT NULL,  -- silver.companies.company_id (other database)
    user_id    text        REFERENCES users ON DELETE CASCADE,
    org_id     text        REFERENCES organizations ON DELETE CASCADE,
    granted_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(user_id, org_id) = 1)
);
CREATE UNIQUE INDEX company_grants_user ON company_grants (company_id, user_id)
    WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX company_grants_org ON company_grants (company_id, org_id)
    WHERE org_id IS NOT NULL;

-- Preferences only: a watchlist entry never grants access.
CREATE TABLE watchlists (
    user_id    text        NOT NULL REFERENCES users ON DELETE CASCADE,
    company_id text        NOT NULL,
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, company_id)
);

-- CDC: deletes must carry the whole old row (who lost access to what), not just the key.
ALTER TABLE users REPLICA IDENTITY FULL;
ALTER TABLE subscriptions REPLICA IDENTITY FULL;
ALTER TABLE company_grants REPLICA IDENTITY FULL;
ALTER TABLE plan_tiers REPLICA IDENTITY FULL;
