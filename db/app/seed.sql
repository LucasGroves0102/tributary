-- Demo users, one per access path. Idempotent: make seed-app.
--   alice  Free, granted Nvidia + Apple directly, Tesla on her watchlist (no access!)
--   bob    Pro, member of Acme, which is granted Nvidia + Microsoft
--   carol  Pro, no grants: sees nothing
--   dave   Free, member of Acme: Nvidia + Microsoft financials only

INSERT INTO organizations (org_id, name) VALUES ('acme', 'Acme Capital')
ON CONFLICT DO NOTHING;

INSERT INTO users (user_id, email, org_id) VALUES
    ('alice', 'alice@example.com', NULL),
    ('bob',   'bob@acme.example',  'acme'),
    ('carol', 'carol@example.com', NULL),
    ('dave',  'dave@acme.example', 'acme')
ON CONFLICT DO NOTHING;

INSERT INTO subscriptions (user_id, plan_id) VALUES
    ('alice', 'free'), ('bob', 'pro'), ('carol', 'pro'), ('dave', 'free')
ON CONFLICT DO NOTHING;

INSERT INTO company_grants (company_id, user_id) VALUES
    ('0001045810', 'alice'),  -- Nvidia
    ('0000320193', 'alice')   -- Apple
ON CONFLICT DO NOTHING;

INSERT INTO company_grants (company_id, org_id) VALUES
    ('0001045810', 'acme'),   -- Nvidia
    ('0000789019', 'acme')    -- Microsoft
ON CONFLICT DO NOTHING;

INSERT INTO watchlists (user_id, company_id) VALUES
    ('alice', '0001318605')   -- Tesla: watched, not granted
ON CONFLICT DO NOTHING;
