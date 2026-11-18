-- Watchlists get the owner-only row-level security every other table of a user's own work has
-- (004, 016, 021...). Until now a watchlist was kept from other users by the application alone
-- (every statement filters on the JWT `sub`): the one table of user content with a single layer,
-- found by tests/access/test_schema.py (as another user's session, a plain SELECT read it).
--
-- Read and written only as its user: the watchlist routes (query_api/watchlist.py, which now set
-- app.user_id in every transaction), the alerts' follows (alerts.py, already as the user) and a
-- new demo, GitHub or password account's first rows (sandbox.grant_demo_access, which now sets
-- app.user_id before it writes them). A deleted user's rows still cascade (referential actions
-- bypass row-level security). Not streamed by Debezium: watching never grants access.
--
-- Locks on hermes: ACCESS EXCLUSIVE on watchlists for an instant (catalog-only changes, no
-- rewrite); lock_timeout 5 s. Deploy with the Query API that sets app.user_id (same release):
-- until the old pods are replaced, they read an empty watchlist and can't add to one or create a
-- sandbox (a minute of rollout).
SET LOCAL lock_timeout = '5s';

ALTER TABLE watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE watchlists FORCE ROW LEVEL SECURITY;
CREATE POLICY owner ON watchlists
    USING (user_id = current_setting('app.user_id', true))
    WITH CHECK (user_id = current_setting('app.user_id', true));
